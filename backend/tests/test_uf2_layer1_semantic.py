"""Kiểm thử Layer 1 (Neural Semantic Layer 1A & 1B) cho UF2: U2-BE-01, U2-BE-02, U2-BE-03, U2-BE-11, U2-SE-02, U2-SE-03."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.task import (
    CandidateAdjustmentResult,
    CandidateStepAdjustment,
    LLMCandidateAdjustmentPayload,
    LLMPlanAdjustmentPayload,
    LLMSessionEnrichmentItem,
    LLMSessionEnrichmentPayload,
    LLMStrictStepPromptItem,
    LLMStrictTaskDecompositionPayload,
    LLMTaskDecompositionPayload,
    NLCandidateStepAdd,
    NLCandidateStepMerge,
    NLCandidateStepSplit,
    PlanAdjustmentRequest,
    PlanAdjustmentResult,
    SessionEnrichmentResult,
    SplitSessionInputItem,
    StepPromptItem,
)
from app.services import ai_service as ai_module
from app.services.ai_service import (
    UF2_CANDIDATE_ADJUSTMENT_SYSTEM_PROMPT,
    UF2_DECOMPOSITION_SYSTEM_PROMPT,
    UF2_SESSION_ENRICHMENT_SYSTEM_PROMPT,
    AIService,
    ExecutionTimingProfile,
)


def _offline_service() -> AIService:
    return AIService(
        app_settings=Settings(
            _env_file=None,
            GEMINI_API_KEY="",
            GROQ_API_KEY="",
            GOOGLE_CREDENTIALS_PATH="",
        )
    )


def _base_candidate_steps(count: int = 3) -> list[dict[str, object]]:
    if count == 3:
        return [
            {
                "client_step_key": "step-1",
                "title": "Khảo sát tài liệu",
                "expected_output": "Danh sách 5 tài liệu chính",
                "order_index": 0,
                "estimated_base_seconds": 1500,
                "remaining_base_seconds": 1500,
                "buffer_multiplier": 1.2,
                "estimate_source": "ai_suggested",
                "status": "pending",
            },
            {
                "client_step_key": "step-2",
                "title": "Viết bản nháp",
                "expected_output": "Bản nháp 3 trang",
                "order_index": 1,
                "estimated_base_seconds": 1800,
                "remaining_base_seconds": 1800,
                "buffer_multiplier": 1.2,
                "estimate_source": "ai_suggested",
                "status": "pending",
            },
            {
                "client_step_key": "step-3",
                "title": "Rà soát và nộp bài",
                "expected_output": "Bản PDF đã kiểm lỗi chính tả",
                "order_index": 2,
                "estimated_base_seconds": 900,
                "remaining_base_seconds": 900,
                "buffer_multiplier": 1.2,
                "estimate_source": "ai_suggested",
                "status": "pending",
            },
        ]
    return [
        {
            "client_step_key": f"step-{i + 1}",
            "title": f"Bước {i + 1}",
            "expected_output": f"Đầu ra {i + 1}",
            "order_index": i,
            "estimated_base_seconds": 1500,
            "remaining_base_seconds": 1500,
            "buffer_multiplier": 1.2,
            "estimate_source": "ai_suggested",
            "status": "pending",
        }
        for i in range(count)
    ]


def _make_split_sessions(
    count: int = 3,
    *,
    step_title: str = "Viết nội dung chính",
    step_expected_output: str = "Bản thảo 4 trang",
    step_micro_task_prompt: str | None = "Mở mục 2 và viết câu mở đầu.",
) -> list[SplitSessionInputItem]:
    return [
        SplitSessionInputItem(
            client_session_key=f"session-step-2-{idx}",
            client_step_key="step-2",
            step_title=step_title,
            step_expected_output=step_expected_output,
            step_micro_task_prompt=step_micro_task_prompt,
            session_index=idx + 1,
            total_sessions_in_step=count,
            work_minutes=25 if idx < count - 1 else 20,
        )
        for idx in range(count)
    ]


# =============================================================================
# U2-BE-01: Strict expected_output wire schema & Prompt guidance
# =============================================================================


def test_u2_be_01_strict_decomposition_schema_requires_non_blank_expected_output() -> None:
    """Wire schema nghiêm ngặt cấm expected_output null/rỗng, giữ tương thích schema cũ."""
    legacy = StepPromptItem(
        order_index=1,
        title="Đọc đề bài",
        expected_output=None,
        estimated_minutes=20,
        micro_task_prompt="Mở PDF đề bài.",
    )
    assert legacy.expected_output is None
    legacy_payload = LLMTaskDecompositionPayload(
        task_title="Bài tập cũ",
        steps=[legacy],
        reasoning="Giữ tương thích ngược.",
    )
    assert legacy_payload.steps[0].expected_output is None

    for bad_output in (None, "", "   ", "ab", "  a  "):
        with pytest.raises(ValidationError):
            LLMStrictStepPromptItem.model_validate(
                {
                    "order_index": 1,
                    "title": "Đọc đề bài",
                    "expected_output": bad_output,
                    "estimated_minutes": 20,
                    "micro_task_prompt": "Mở PDF đề bài.",
                }
            )

    strict_step = LLMStrictStepPromptItem(
        order_index=5,
        title="Đọc đề bài",
        expected_output="  Gạch đầu dòng 3 yêu cầu chính  ",
        estimated_minutes=20,
        micro_task_prompt="Mở PDF đề bài.",
    )
    assert strict_step.expected_output == "Gạch đầu dòng 3 yêu cầu chính"

    payload = LLMStrictTaskDecompositionPayload(
        task_title="Bài tập tuần",
        steps=[
            LLMStrictStepPromptItem(
                order_index=8,
                title="Bước hai",
                expected_output="Xong phần hai",
                estimated_minutes=25,
                micro_task_prompt="Mở file Word.",
            ),
            strict_step,
        ],
        reasoning="Chia 2 bước tuần tự.",
    )
    assert [s.order_index for s in payload.steps] == [1, 2]
    assert all(isinstance(s, LLMStrictStepPromptItem) for s in payload.steps)

    json_schema = LLMStrictTaskDecompositionPayload.model_json_schema()
    step_def = json_schema["$defs"]["LLMStrictStepPromptItem"]
    assert "expected_output" in step_def["required"]
    assert step_def["properties"]["expected_output"]["type"] == "string"
    assert step_def["properties"]["expected_output"]["minLength"] == 3


@pytest.mark.asyncio
async def test_u2_be_01_decompose_task_uses_strict_schema_and_degrades_on_blank_json() -> None:
    """decompose_task() dùng LLMStrictTaskDecompositionPayload, prompt đủ guardrails và fallback Tier 3."""
    service = _offline_service()
    captured: dict[str, object] = {}

    async def _mock_hedged(**kwargs):
        captured.update(kwargs)
        return (
            LLMStrictTaskDecompositionPayload(
                task_title="Ôn thi",
                steps=[
                    LLMStrictStepPromptItem(
                        order_index=1,
                        title="Tóm tắt chương 1",
                        expected_output="Trang ghi chú 10 ý chính",
                        estimated_minutes=30,
                        micro_task_prompt="Mở vở ghi và viết tiêu đề chương 1.",
                    )
                ],
                reasoning="Chia nhỏ theo chương.",
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_mock_hedged):
        result = await service.decompose_task(
            task_title="Ôn thi",
            scope="3 chương đầu",
            extra_requirements=["Tối đa 90 phút/ngày", "Ưu tiên làm bài tập mẫu"],
            total_free_hours=6.5,
        )

    assert captured["schema_cls"] is LLMStrictTaskDecompositionPayload
    prompt_data = json.loads(str(captured["user_prompt"]))
    assert prompt_data["total_free_hours"] == 6.5
    assert prompt_data["extra_requirements"] == ["Tối đa 90 phút/ngày", "Ưu tiên làm bài tập mẫu"]
    assert result.generation_tier == "tier_1_gemini"
    assert result.steps[0].expected_output == "Trang ghi chú 10 ý chính"

    assert "total_free_hours" in UF2_DECOMPOSITION_SYSTEM_PROMPT
    assert "extra_requirements" in UF2_DECOMPOSITION_SYSTEM_PROMPT
    assert "expected_output" in UF2_DECOMPOSITION_SYSTEM_PROMPT
    assert "order_index" in UF2_DECOMPOSITION_SYSTEM_PROMPT
    assert "planning_horizon_days" in UF2_DECOMPOSITION_SYSTEM_PROMPT
    assert "không tự cắt xén khối lượng" in UF2_DECOMPOSITION_SYSTEM_PROMPT.lower()

    gemini_svc = AIService(
        app_settings=Settings(_env_file=None, GEMINI_API_KEY="AIzaSyFakeKey", GROQ_API_KEY="")
    )
    bad_json = json.dumps(
        {
            "task_title": "Ôn thi",
            "requires_clarification": False,
            "clarification_question": None,
            "steps": [
                {
                    "order_index": 1,
                    "title": "Đọc chương 1",
                    "expected_output": None,
                    "estimated_minutes": 25,
                    "micro_task_prompt": "Mở sách trang 1.",
                }
            ],
            "reasoning": "Nhanh.",
        }
    )
    fake_gemini_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=AsyncMock(return_value=SimpleNamespace(text=bad_json))
            )
        )
    )
    with patch.object(gemini_svc, "_get_gemini_client", return_value=fake_gemini_client):
        fallback_res = await gemini_svc.decompose_task(task_title="Ôn thi")

    assert fallback_res.generation_tier == "tier_3_rule_based"
    assert len(fallback_res.steps) == 3
    assert all(step.expected_output and step.expected_output.strip() for step in fallback_res.steps)


# =============================================================================
# U2-BE-02: Fallback decomposition unexpanded_scope for long horizons (> 14d)
# =============================================================================


def test_u2_be_02_fallback_decompose_sets_unexpanded_scope_for_long_horizon_without_proposal() -> None:
    """_fallback_decompose_task giữ unexpanded_scope=None với <=14d hoặc user_step_proposal, và đặt unexpanded_scope khi >14d."""
    service = _offline_service()

    short_res = service._fallback_decompose_task(
        "Bài tập tuần",
        scope="5 bài tập",
        planning_horizon_days=14,
        total_project_days=14,
    )
    assert short_res.unexpanded_scope is None
    assert len(short_res.steps) == 3

    long_res = service._fallback_decompose_task(
        "Khóa luận",
        scope="20 trang",
        planning_horizon_days=14,
        total_project_days=30,
    )
    assert len(long_res.steps) == 3
    assert (
        long_res.unexpanded_scope
        == "Phần tiếp theo của 'Khóa luận' (20 trang) sau chặng 14 ngày đầu."
    )

    long_no_scope = service._fallback_decompose_task(
        "Khóa luận",
        planning_horizon_days=14,
        total_project_days=45,
    )
    assert long_no_scope.unexpanded_scope == "Phần tiếp theo của 'Khóa luận' sau chặng 14 ngày đầu."

    long_with_proposal = service._fallback_decompose_task(
        "Khóa luận",
        user_step_proposal="1. Đọc tài liệu 30p; 2. Viết đề cương 45p",
        planning_horizon_days=14,
        total_project_days=30,
    )
    assert long_with_proposal.unexpanded_scope is None
    assert len(long_with_proposal.steps) == 2


# =============================================================================
# U2-BE-03: Deprecation markers on legacy UF2 plan adjustment contracts
# =============================================================================


def test_u2_be_03_legacy_plan_adjustment_marked_deprecated() -> None:
    """Các contract & method cũ của adjust_plan_by_nl được đánh dấu DEPRECATED nhưng vẫn hoạt động."""
    for target in (
        LLMPlanAdjustmentPayload,
        PlanAdjustmentResult,
        PlanAdjustmentRequest,
        AIService.adjust_plan_by_nl,
    ):
        assert target.__doc__ is not None
        assert "DEPRECATED" in target.__doc__

    module_src = inspect.getsource(ai_module)
    assert "[DEPRECATED" in module_src
    assert "UF2_PLAN_ADJUSTMENT_SYSTEM_PROMPT" in module_src


# =============================================================================
# U2-BE-11: Expanded NL Candidate Adjustment, Unit Guards & Validator Ordering
# =============================================================================


def test_u2_be_11_candidate_step_adjustment_and_structural_dtos() -> None:
    """Kiểm tra guard đơn vị phút/giây, micro_task_text và 3 DTO thao tác cấu trúc."""
    with pytest.raises(ValidationError):
        CandidateStepAdjustment(client_step_key="step-1", estimated_base_seconds=45)
    with pytest.raises(ValidationError):
        CandidateStepAdjustment(client_step_key="step-1", estimated_base_seconds=28801)
    with pytest.raises(ValidationError):
        CandidateStepAdjustment(client_step_key="step-1")
    with pytest.raises(ValidationError):
        CandidateStepAdjustment(
            client_step_key="step-1", estimated_minutes=30, estimated_base_seconds=1500
        )
    with pytest.raises(ValidationError):
        CandidateStepAdjustment(client_step_key="step-1", micro_task_text="   ")

    from_minutes = CandidateStepAdjustment(
        client_step_key=" step-1 ",
        estimated_minutes=45,
        micro_task_text=" Mở file chính ",
    )
    assert from_minutes.client_step_key == "step-1"
    assert from_minutes.estimated_base_seconds == 2700
    assert from_minutes.micro_task_text == "Mở file chính"

    add_min = NLCandidateStepAdd(
        title=" Kiểm thử tích hợp ",
        expected_output=" Toàn bộ test xanh ",
        estimated_minutes=25,
        micro_task_prompt=" Chạy pytest -q ",
        insert_at_index=1,
    )
    assert add_min.estimated_base_seconds == 1500
    assert add_min.resolved_base_seconds == 1500

    add_sec = NLCandidateStepAdd(
        title="Chạy benchmark",
        expected_output="Log đo độ trễ",
        estimated_base_seconds=1500,
        micro_task_prompt="Mở terminal benchmark",
    )
    assert add_sec.estimated_minutes == 25
    assert add_sec.resolved_base_seconds == 1500

    with pytest.raises(ValidationError):
        NLCandidateStepAdd(
            title="Thiếu thời lượng",
            expected_output="Có đầu ra",
            micro_task_prompt="Mở file",
        )

    merge_op = NLCandidateStepMerge(
        source_step_keys=["step-1", "step-2"],
        title="Chuẩn bị và viết nháp",
        expected_output="Xong bản nháp đầu tiên",
    )
    assert merge_op.estimated_minutes is None
    with pytest.raises(ValidationError):
        NLCandidateStepMerge(
            source_step_keys=["step-1", "step-1"],
            title="Trùng key",
            expected_output="Đầu ra hợp lệ",
        )

    split_op = NLCandidateStepSplit(
        source_step_key="step-2",
        new_steps=[add_min, add_sec],
    )
    assert split_op.source_step_key == "step-2"
    assert len(split_op.new_steps) == 2


def test_u2_be_11_validator_ordering_cleans_all_malformed_blocked_arrays_only_when_blocked() -> None:
    """clean_blocked_raw_edits xóa sạch 5 mảng malformed khi bị block; vẫn chặn chặt khi không bị block."""
    malformed_blocked_cases: list[dict[str, object]] = [
        {"step_edits": [{"client_step_key": "step-1"}]},
        {"step_edits": [{"client_step_key": "step-1", "estimated_minutes": 0}]},
        {"step_edits": ["not-a-dict-item"]},
        {"delete_step_keys": ["", "   "]},
        {"add_steps": [{"title": ""}]},
        {"merge_steps": [{"source_step_keys": ["only-one"]}]},
        {"split_steps": [{"source_step_key": ""}]},
    ]
    blocked_flags = (
        {
            "requires_clarification": True,
            "clarification_question": "Bạn muốn đổi bước nào cụ thể?",
            "requires_draft_update": False,
            "unsupported_changes": [],
        },
        {
            "requires_clarification": False,
            "clarification_question": None,
            "requires_draft_update": True,
            "unsupported_changes": [],
        },
        {
            "requires_clarification": False,
            "clarification_question": None,
            "requires_draft_update": False,
            "unsupported_changes": ["Đổi cài đặt pomodoro"],
        },
    )
    for malformed in malformed_blocked_cases:
        for flags in blocked_flags:
            payload = LLMCandidateAdjustmentPayload.model_validate(
                {
                    **malformed,
                    **flags,
                    "assistant_note": "Mình cần bạn xác nhận lại.",
                }
            )
            assert payload.requires_clarification is True
            assert payload.step_edits == []
            assert payload.add_steps == []
            assert payload.delete_step_keys == []
            assert payload.merge_steps == []
            assert payload.split_steps == []

    blocked_result = CandidateAdjustmentResult.model_validate(
        {
            "step_edits": [{"client_step_key": "step-1", "title": "Tên tạm"}],
            "add_steps": [{"title": ""}],
            "delete_step_keys": ["   "],
            "merge_steps": [{"source_step_keys": ["step-1"]}],
            "split_steps": [{"source_step_key": ""}],
            "requires_clarification": True,
            "clarification_question": "Xác nhận giúp mình nhé?",
            "requires_draft_update": False,
            "unsupported_changes": [],
            "assistant_note": "Chưa đổi.",
            "generation_tier": "tier_1_gemini",
        }
    )
    assert blocked_result.step_edits == []
    assert blocked_result.add_steps == []
    assert blocked_result.delete_step_keys == []
    assert blocked_result.merge_steps == []
    assert blocked_result.split_steps == []

    # Khi không bị block, toàn bộ mảng malformed vẫn bị ValidationError chặn
    for malformed in malformed_blocked_cases:
        with pytest.raises(ValidationError):
            LLMCandidateAdjustmentPayload.model_validate(
                {
                    **malformed,
                    "requires_clarification": False,
                    "clarification_question": None,
                    "requires_draft_update": False,
                    "unsupported_changes": [],
                    "assistant_note": "Đổi bước",
                }
            )

    # Giữ an toàn edge cases non-list unsupported_changes / thiếu assistant_note dù bị block
    with pytest.raises(ValidationError):
        LLMCandidateAdjustmentPayload.model_validate(
            {
                "requires_clarification": True,
                "clarification_question": "Hỏi lại?",
                "requires_draft_update": False,
                "unsupported_changes": "bad-non-list",
                "assistant_note": "Chưa đổi.",
            }
        )
    with pytest.raises(ValidationError):
        LLMCandidateAdjustmentPayload.model_validate(
            {
                "requires_clarification": True,
                "clarification_question": "Hỏi lại?",
                "requires_draft_update": False,
                "unsupported_changes": [],
                "assistant_note": "   ",
            }
        )


@pytest.mark.asyncio
async def test_u2_be_11_adjust_candidate_by_nl_structural_ops_and_guards() -> None:
    """adjust_candidate_by_nl hỗ trợ 5 nhóm thao tác, chuyển đổi phút->giây, kiểm biên 1..20 và chặn xung đột."""
    service = _offline_service()
    steps = _base_candidate_steps(3)

    for token in (
        "add_steps",
        "delete_step_keys",
        "merge_steps",
        "split_steps",
        "micro_task_text",
        "estimated_minutes",
        "1..20",
    ):
        assert token in UF2_CANDIDATE_ADJUSTMENT_SYSTEM_PROMPT

    # 1) estimated_minutes=45 trên step-2 (1800s) -> đổi thành estimated_base_seconds=2700, bỏ estimated_minutes
    #    cùng lúc add_steps, delete_step_keys và no-op estimated_minutes=25 trên step-1 (1500s)
    async def _structural_and_minutes_ok(**_):
        return (
            LLMCandidateAdjustmentPayload(
                step_edits=[
                    CandidateStepAdjustment(client_step_key="step-1", estimated_minutes=25),
                    CandidateStepAdjustment(
                        client_step_key="step-2",
                        estimated_minutes=45,
                        micro_task_text="Mở bản nháp và viết thêm đoạn mở rộng",
                    ),
                ],
                add_steps=[
                    NLCandidateStepAdd(
                        title="Kiểm tra trích dẫn",
                        expected_output="Danh mục tài liệu tham khảo chuẩn",
                        estimated_minutes=15,
                        micro_task_prompt="Mở mục References.",
                        insert_at_index=2,
                    )
                ],
                delete_step_keys=["step-3"],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Đã đề xuất cập nhật bước 2 lên 45 phút, thêm kiểm tra trích dẫn và xóa bước 3.",
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_structural_and_minutes_ok):
        res = await service.adjust_candidate_by_nl(
            "Sửa bước 2 thành 45 phút, xóa bước 3, thêm bước kiểm tra trích dẫn",
            current_steps=steps,
        )

    assert res.requires_clarification is False
    assert res.generation_tier == "tier_1_gemini"
    assert len(res.step_edits) == 1
    assert res.step_edits[0].client_step_key == "step-2"
    assert res.step_edits[0].estimated_base_seconds == 2700
    assert res.step_edits[0].estimated_minutes is None
    assert res.step_edits[0].micro_task_text == "Mở bản nháp và viết thêm đoạn mở rộng"
    assert len(res.add_steps) == 1
    assert res.add_steps[0].resolved_base_seconds == 900
    assert res.delete_step_keys == ["step-3"]

    # 2) Exact 1-step lower boundary (3 - 2 = 1 -> ALLOWED)
    async def _boundary_one_ok(**_):
        return (
            LLMCandidateAdjustmentPayload(
                delete_step_keys=["step-2", "step-3"],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Giữ lại đúng 1 bước.",
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_boundary_one_ok):
        one_res = await service.adjust_candidate_by_nl("Xóa bước 2 và 3", current_steps=steps)
    assert one_res.requires_clarification is False
    assert one_res.delete_step_keys == ["step-2", "step-3"]

    # 3) Exact 20-step upper boundary (19 + 1 = 20 -> ALLOWED; 20 + 1 = 21 -> REJECTED)
    steps_19 = _base_candidate_steps(19)
    steps_20 = _base_candidate_steps(20)

    async def _boundary_twenty_add_ok(**_):
        return (
            LLMCandidateAdjustmentPayload(
                add_steps=[
                    NLCandidateStepAdd(
                        title="Bước 20",
                        expected_output="Đầu ra 20",
                        estimated_minutes=15,
                        micro_task_prompt="Mở tài liệu 20.",
                    )
                ],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Đủ 20 bước.",
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_boundary_twenty_add_ok):
        twenty_res = await service.adjust_candidate_by_nl("Thêm bước 20", current_steps=steps_19)
    assert twenty_res.requires_clarification is False
    assert len(twenty_res.add_steps) == 1

    with (
        patch.object(service, "_execute_hedged_structured", side_effect=_boundary_twenty_add_ok),
        pytest.raises(TimeoutError),
    ):
        await service.adjust_candidate_by_nl("Thêm bước 21", current_steps=steps_20)

    # 3b) Split 1 -> 2 trên 19 steps => projected 20 (ALLOWED), Split 1 -> 3 trên 19 steps => projected 21 (REJECTED)
    async def _boundary_split_ok(**_):
        return (
            LLMCandidateAdjustmentPayload(
                split_steps=[
                    NLCandidateStepSplit(
                        source_step_key="step-1",
                        new_steps=[
                            NLCandidateStepAdd(
                                title="Phần A",
                                expected_output="Xong phần A",
                                estimated_minutes=15,
                                micro_task_prompt="Mở phần A.",
                            ),
                            NLCandidateStepAdd(
                                title="Phần B",
                                expected_output="Xong phần B",
                                estimated_minutes=15,
                                micro_task_prompt="Mở phần B.",
                            ),
                        ],
                    )
                ],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Tách thành 2 bước -> tổng 20.",
            ),
            "tier_1_gemini",
        )

    async def _boundary_split_overflow(**_):
        return (
            LLMCandidateAdjustmentPayload(
                split_steps=[
                    NLCandidateStepSplit(
                        source_step_key="step-1",
                        new_steps=[
                            NLCandidateStepAdd(
                                title=f"Phần {idx}",
                                expected_output=f"Xong phần {idx}",
                                estimated_minutes=10,
                                micro_task_prompt=f"Mở phần {idx}.",
                            )
                            for idx in range(3)
                        ],
                    )
                ],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Tách thành 3 bước -> tổng 21.",
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_boundary_split_ok):
        split_20_res = await service.adjust_candidate_by_nl(
            "Tách bước 1 làm 2", current_steps=steps_19
        )
    assert split_20_res.requires_clarification is False
    assert len(split_20_res.split_steps) == 1

    with (
        patch.object(service, "_execute_hedged_structured", side_effect=_boundary_split_overflow),
        pytest.raises(TimeoutError),
    ):
        await service.adjust_candidate_by_nl("Tách bước 1 làm 3", current_steps=steps_19)

    # 4) Overlapping two merge_steps sharing the same step key -> rejected
    async def _overlapping_merges(**_):
        return (
            LLMCandidateAdjustmentPayload(
                merge_steps=[
                    NLCandidateStepMerge(
                        source_step_keys=["step-1", "step-2"],
                        title="Gộp 1-2",
                        expected_output="Xong 1-2",
                    ),
                    NLCandidateStepMerge(
                        source_step_keys=["step-2", "step-3"],
                        title="Gộp 2-3",
                        expected_output="Xong 2-3",
                    ),
                ],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Gộp chồng chéo.",
            ),
            "tier_1_gemini",
        )

    with (
        patch.object(service, "_execute_hedged_structured", side_effect=_overlapping_merges),
        pytest.raises(TimeoutError),
    ):
        await service.adjust_candidate_by_nl("Gộp 1-2 và gộp 2-3", current_steps=steps)

    # 5) Pure no-op via estimated_minutes=25 matching current 1500s -> becomes requires_clarification=True
    async def _pure_noop(**_):
        return (
            LLMCandidateAdjustmentPayload(
                step_edits=[CandidateStepAdjustment(client_step_key="step-1", estimated_minutes=25)],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Giữ 25 phút.",
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_pure_noop):
        noop_res = await service.adjust_candidate_by_nl("Bước 1 làm 25 phút", current_steps=steps)
    assert noop_res.requires_clarification is True
    assert noop_res.step_edits == []

    # 6) Conflicting ops on same client_step_key -> rejected
    async def _conflicting(**_):
        return (
            LLMCandidateAdjustmentPayload(
                step_edits=[CandidateStepAdjustment(client_step_key="step-1", title="Đổi tên")],
                delete_step_keys=["step-1"],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Xung đột.",
            ),
            "tier_1_gemini",
        )

    with (
        patch.object(service, "_execute_hedged_structured", side_effect=_conflicting),
        pytest.raises(TimeoutError),
    ):
        await service.adjust_candidate_by_nl("Sửa và xóa bước 1", current_steps=steps)

    # 7) Projected step count == 0 (delete all 3 steps) -> rejected
    async def _delete_all(**_):
        return (
            LLMCandidateAdjustmentPayload(
                delete_step_keys=["step-1", "step-2", "step-3"],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Xóa hết.",
            ),
            "tier_1_gemini",
        )

    with (
        patch.object(service, "_execute_hedged_structured", side_effect=_delete_all),
        pytest.raises(TimeoutError),
    ):
        await service.adjust_candidate_by_nl("Xóa hết các bước", current_steps=steps)

    # 8) Unknown key in merge_steps -> rejected
    async def _unknown_merge(**_):
        return (
            LLMCandidateAdjustmentPayload(
                merge_steps=[
                    NLCandidateStepMerge(
                        source_step_keys=["step-1", "step-999"],
                        title="Gộp",
                        expected_output="Xong gộp",
                    )
                ],
                requires_clarification=False,
                clarification_question=None,
                requires_draft_update=False,
                unsupported_changes=[],
                assistant_note="Gộp.",
            ),
            "tier_1_gemini",
        )

    with (
        patch.object(service, "_execute_hedged_structured", side_effect=_unknown_merge),
        pytest.raises(TimeoutError),
    ):
        await service.adjust_candidate_by_nl("Gộp bước 1 và 999", current_steps=steps)


# =============================================================================
# U2-SE-02 & U2-SE-03: Frame C Session Enrichment Schemas & Service
# =============================================================================


def test_u2_se_02_frame_c_config_profile_and_enrichment_schemas() -> None:
    """Kiểm tra cấu hình Khung C và validation đúng chuẩn của các DTO Session Enrichment."""
    cfg = Settings(_env_file=None)
    assert cfg.AI_UF2_ENRICH_TIMEOUT_SECONDS == 3.0
    assert cfg.AI_UF2_ENRICH_PRIMARY_PRIORITY_SECONDS == 1.8
    assert cfg.AI_UF2_ENRICH_WARM_BACKUP_DELAY_SECONDS == 1.0
    assert cfg.AI_UF2_ENRICH_GROQ_HEDGE_DELAY_SECONDS == 1.6

    profile = AIService(app_settings=cfg)._session_enrichment_profile()
    assert profile.hard_timeout == 3.0
    assert profile.primary_priority_until == 1.8
    assert profile.warm_backup_at == 1.0
    assert profile.groq_hedge_at == 1.6

    for token in (
        "sessions",
        "client_session_key",
        "title",
        "session_goal",
        "expected_output",
        "micro_task_text",
        "session_index >= 2",
    ):
        assert token in UF2_SESSION_ENRICHMENT_SYSTEM_PROMPT

    # step_expected_output bắt buộc non-blank, step_micro_task_prompt tùy chọn, total_sessions_in_step >= 2
    for bad_expected in (None, "", "   "):
        with pytest.raises(ValidationError):
            SplitSessionInputItem.model_validate(
                {
                    "client_session_key": "session-step-1-0",
                    "client_step_key": "step-1",
                    "step_title": "Viết chương 2",
                    "step_expected_output": bad_expected,
                    "session_index": 1,
                    "total_sessions_in_step": 2,
                    "work_minutes": 25,
                }
            )
    with pytest.raises(ValidationError):
        SplitSessionInputItem(
            client_session_key="session-step-1-0",
            client_step_key="step-1",
            step_title="Viết chương 2",
            step_expected_output="Bản thảo",
            session_index=1,
            total_sessions_in_step=1,
            work_minutes=25,
        )
    with pytest.raises(ValidationError):
        SplitSessionInputItem(
            client_session_key="session-step-1-2",
            client_step_key="step-1",
            step_title="Viết chương 2",
            step_expected_output="Bản thảo",
            session_index=4,
            total_sessions_in_step=3,
            work_minutes=25,
        )

    valid_input = SplitSessionInputItem(
        client_session_key="session-step-1-0",
        client_step_key="step-1",
        step_title="Viết chương 2",
        step_expected_output="OK",
        step_micro_task_prompt="  Mở Word  ",
        session_index=1,
        total_sessions_in_step=2,
        work_minutes=25,
    )
    assert valid_input.step_micro_task_prompt == "Mở Word"

    for bad_item_val in ("", "   ", "ab", "  a  "):
        with pytest.raises(ValidationError):
            LLMSessionEnrichmentItem(
                client_session_key="s1",
                title=bad_item_val,
                session_goal="Làm phần 1",
                expected_output="Đầu ra phần 1",
                micro_task_text="Mở file nháp.",
            )

    empty_payload = LLMSessionEnrichmentPayload()
    assert empty_payload.sessions == []

    with pytest.raises(ValidationError):
        LLMSessionEnrichmentPayload(
            sessions=[
                LLMSessionEnrichmentItem(
                    client_session_key="s1",
                    title="Phiên 1",
                    session_goal="Làm phần mở đầu",
                    expected_output="Xong mở đầu",
                    micro_task_text="Mở file nháp.",
                ),
                LLMSessionEnrichmentItem(
                    client_session_key="s1",
                    title="Phiên 2",
                    session_goal="Làm phần thân bài",
                    expected_output="Xong thân bài",
                    micro_task_text="Mở file chính.",
                ),
            ]
        )


@pytest.mark.asyncio
async def test_u2_se_03_single_fallback_bounds_and_enrich_split_sessions() -> None:
    """Kiểm tra single enrichment, giới hạn chuỗi <3 / >160 / >300, partial merge, 0-match & timeout."""
    service = _offline_service()

    # 1) build_single_session_enrichment với chuỗi <3 ký tự và chuỗi siêu dài
    short_single = service.build_single_session_enrichment(
        client_session_key="session-step-1-0",
        step_title="AB",
        step_expected_output="OK",
        micro_task_text="Go",
    )
    assert 3 <= len(short_single.title) <= 160
    assert 3 <= len(short_single.session_goal) <= 300
    assert 3 <= len(short_single.expected_output) <= 300
    assert 3 <= len(short_single.micro_task_text) <= 300

    long_title = "Phân tích và triển khai hệ thống kiểm thử tự động tích hợp liên tục rất dài " * 4
    long_output = "Báo cáo tổng hợp đầy đủ các biểu đồ và bảng số liệu chi tiết rất dài " * 8
    long_single = service.build_single_session_enrichment(
        client_session_key="session-step-1-1",
        step_title=long_title[:250],
        step_expected_output=long_output[:480],
        micro_task_text="Mở trình duyệt và tải báo cáo " * 15,
    )
    assert 3 <= len(long_single.title) <= 160
    assert 3 <= len(long_single.session_goal) <= 300
    assert 3 <= len(long_single.expected_output) <= 300
    assert 3 <= len(long_single.micro_task_text) <= 300

    # 2) _fallback_enrich_split_sessions giữ nguyên hậu tố (Phần i/N) khi step_title dài và xử lý OK (<3 chars)
    long_split = _make_split_sessions(
        3,
        step_title=long_title[:255],
        step_expected_output="OK",
        step_micro_task_prompt="Go",
    )
    fb_res = service._fallback_enrich_split_sessions(long_split)
    assert fb_res.generation_tier == "tier_3_rule_based"
    assert len(fb_res.sessions) == 3
    for idx, enriched in enumerate(fb_res.sessions, start=1):
        assert enriched.title.endswith(f" (Phần {idx}/3)")
        assert 3 <= len(enriched.title) <= 160
        assert 3 <= len(enriched.session_goal) <= 300
        assert 3 <= len(enriched.expected_output) <= 300
        assert 3 <= len(enriched.micro_task_text) <= 300
        if idx >= 2:
            assert f"làm tiếp phần {idx}/3" in enriched.micro_task_text

    # 3) enrich_split_sessions([]) trả rỗng ngay 0ms, không gọi provider
    with patch.object(service, "_execute_hedged_structured") as hedged_mock:
        empty_res = await service.enrich_split_sessions([])
    hedged_mock.assert_not_called()
    assert isinstance(empty_res, SessionEnrichmentResult)
    assert empty_res.sessions == []
    assert empty_res.generation_tier == "tier_3_rule_based"

    split_inputs = _make_split_sessions(3)
    with pytest.raises(ValueError):
        await service.enrich_split_sessions([split_inputs[0], split_inputs[0]])

    # 4) Full/Partial match + kiểm tra profile Frame C & force_minimal_thinking=True
    captured_call: dict[str, object] = {}

    async def _partial_llm(**kwargs):
        captured_call.update(kwargs)
        return (
            LLMSessionEnrichmentPayload(
                sessions=[
                    LLMSessionEnrichmentItem(
                        client_session_key="session-step-2-0",
                        title="Viết nội dung chính (Phần 1/3)",
                        session_goal="Phác thảo mở đầu và 3 luận điểm chính",
                        expected_output="Hoàn thành dàn ý chi tiết mục 2",
                        micro_task_text="Mở mục 2 và viết câu mở đầu.",
                    ),
                    LLMSessionEnrichmentItem(
                        client_session_key="session-step-2-2",
                        title="Viết nội dung chính (Phần 3/3)",
                        session_goal="Hoàn thiện kết luận và kiểm tra mạch văn",
                        expected_output="Bản thảo 4 trang",
                        micro_task_text="Mở lại bản thảo để rà soát và hoàn thiện phần 3/3.",
                    ),
                    LLMSessionEnrichmentItem(
                        client_session_key="hallucinated-key",
                        title="Bịa đặt",
                        session_goal="Không tồn tại",
                        expected_output="Không dùng",
                        micro_task_text="Bỏ qua.",
                    ),
                ]
            ),
            "tier_2_groq",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_partial_llm):
        partial_res = await service.enrich_split_sessions(split_inputs, task_title="Viết báo cáo")

    assert captured_call["schema_cls"] is LLMSessionEnrichmentPayload
    assert captured_call["force_minimal_thinking"] is True
    used_profile = captured_call["profile"]
    assert isinstance(used_profile, ExecutionTimingProfile)
    assert used_profile.hard_timeout == 3.0
    assert partial_res.generation_tier == "tier_2_groq"
    assert [s.client_session_key for s in partial_res.sessions] == [
        "session-step-2-0",
        "session-step-2-1",
        "session-step-2-2",
    ]
    assert partial_res.sessions[0].title == "Viết nội dung chính (Phần 1/3)"
    assert partial_res.sessions[1].title == "Viết nội dung chính (Phần 2/3)"
    assert "làm tiếp phần 2/3" in partial_res.sessions[1].micro_task_text
    assert partial_res.sessions[2].expected_output == "Bản thảo 4 trang"

    # 5) Zero matches -> full fallback tier_3_rule_based
    async def _zero_match_llm(**_):
        return (
            LLMSessionEnrichmentPayload(
                sessions=[
                    LLMSessionEnrichmentItem(
                        client_session_key="wrong-key-only",
                        title="Sai khóa",
                        session_goal="Sai khóa",
                        expected_output="Sai khóa",
                        micro_task_text="Mở file bất kỳ.",
                    )
                ]
            ),
            "tier_1_gemini",
        )

    with patch.object(service, "_execute_hedged_structured", side_effect=_zero_match_llm):
        zero_res = await service.enrich_split_sessions(split_inputs)
    assert zero_res.generation_tier == "tier_3_rule_based"
    assert len(zero_res.sessions) == 3

    # 6) TimeoutError -> full fallback tier_3_rule_based
    with patch.object(
        service, "_execute_hedged_structured", side_effect=TimeoutError("Frame C timeout")
    ):
        timeout_res = await service.enrich_split_sessions(split_inputs)
    assert timeout_res.generation_tier == "tier_3_rule_based"
    assert len(timeout_res.sessions) == 3
