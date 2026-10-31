"""Dịch vụ tích hợp LLM đa tầng có Hedged Warm Backup:
Gemini 3.8 Flash (Primary) -> Gemini 3.5 Flash-Lite (Warm Backup) -> Groq Qwen 3.8 / GPT-OSS (Tier 2) -> Rule-based (Tier 3).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, NamedTuple, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google import genai
from google.genai import types
from google.oauth2 import service_account
from groq import AsyncGroq
from pydantic import BaseModel

from app.core.config import Settings, settings
from app.schemas.schedule import (
    LLMRescheduleEmpathyPayload,
    RescheduleEmpatheticResult,
)
from app.schemas.task import (
    GenerationTier,
    LLMTaskDecompositionPayload,
    LLMTaskExtractionPayload,
    StepPromptItem,
    TaskDecompositionResult,
    TaskExtractionResult,
)

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_CLOUD_PLATFORM_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class ExecutionTimingProfile(NamedTuple):
    """Cấu hình các mốc thời gian (giây) cho bộ điều phối Quality-First Hedged Execution."""

    hard_timeout: float
    primary_priority_until: float
    warm_backup_at: float
    groq_hedge_at: float


# ==============================================================================
# SYSTEM PROMPTS TIẾNG VIỆT CHUẨN NGHIỆP VỤ ADHD-I (UF1, UF2, UF6)
# ==============================================================================

UF1_EXTRACTION_SYSTEM_PROMPT = """Bạn là trợ lý lập kế hoạch chuyên biệt dành cho người trưởng thành mắc hội chứng giảm chú ý (ADHD-I) trên ứng dụng ProPlan VA.
Nhiệm vụ của bạn ở bước UF1 là đọc nội dung công việc người dùng vừa nói/nhập và trích xuất chính xác 4 thông tin bắt buộc:
1. `content`: Nội dung công việc cần làm.
2. `scope`: Phạm vi hoặc đầu ra cụ thể mong muốn (ví dụ: bao nhiêu trang, bao nhiêu slide, tiêu chuẩn hoàn thành).
3. `free_time_slots`: Các khung giờ rảnh người dùng có thể dành cho việc này.
4. `deadline`: Thời hạn hoàn thành (ISO 8601 có múi giờ).

QUY TẮC BẤT BIẾN (STRICT GUARDRAILS — TUYỆT ĐỐI TUÂN THỦ):
- KHÔNG TỰ ĐOÁN DỮ LIỆU (ZERO HALLUCINATION): Nếu người dùng chưa nêu rõ `scope`, `free_time_slots` hoặc `deadline`, bắt buộc trả về `null` (hoặc mảng rỗng `[]` đối với `free_time_slots`). Tuyệt đối không tự gán giờ rảnh mặc định hay tự bịa hạn chót cuối tuần/cuối ngày.
- PHÁT HIỆN MÂU THUẪN: Đối chiếu với mốc thời gian hiện tại (`current_datetime`) và danh sách lịch bận (`busy_intervals`) được cung cấp. Đặt `has_conflict = true` nếu hạn chót nằm trong quá khứ hoặc toàn bộ khung giờ rảnh người dùng nêu bị trùng với lịch bận.
- GỘP CÂU HỎI LÀM RÕ TRONG 1 VÒNG DUY NHẤT (SINGLE-ROUND CLARIFICATION): Nếu thiếu bất kỳ thông tin nào trong 4 trường bắt buộc hoặc có mâu thuẫn (`has_conflict = true`), đặt `is_complete = false` và gom TẤT CẢ các câu hỏi cần làm rõ vào mảng `clarification_questions` một cách ngắn gọn, dịu dàng, dễ trả lời ngay trong 1 màn hình duy nhất.
- CHỈ ĐẶT `is_complete = true` KHI VÀ CHỈ KHI cả 4 trường (`content`, `scope`, `free_time_slots`, `deadline`) đều đã rõ ràng, cụ thể và `has_conflict = false` (khi đó `clarification_questions` phải là mảng rỗng `[]`).
- Giọng điệu tiếng Việt ấm áp, súc tích, giảm tải nhận thức theo chuẩn W3C COGA."""

UF2_DECOMPOSITION_SYSTEM_PROMPT = """Bạn là chuyên gia hỗ trợ chức năng điều hành (Executive Function Coach) cho người mắc ADHD-I trên ứng dụng ProPlan VA.
Nhiệm vụ của bạn ở bước UF2 là chủ động bẻ nhỏ một công việc đã xác nhận thành phương án các bước chuẩn (Default Decomposition Proposal) để giúp người dùng vượt qua hội chứng Tê liệt khởi động (ADHD Blank Canvas / Task Initiation Paralysis).

QUY TẮC BẤT BIẾN (STRICT GUARDRAILS — TUYỆT ĐỐI TUÂN THỦ):
1. SỐ LƯỢNG BƯỚC VỪA SỨC: Chia công việc thành từ 2 đến 5 bước tuần tự (`steps`), mỗi bước có đầu ra cụ thể (`expected_output`) và thời lượng làm chính dự kiến gốc hợp lý (`estimated_minutes`, thường từ 15 đến 60 phút, CHƯA nhân hệ số đệm buffer).
2. BẮT BUỘC CÓ MICRO-TASK KHỞI ĐỘNG 2 PHÚT (`micro_task_prompt`): Mỗi bước phải đi kèm duy nhất 1 hành động vật lý siêu nhỏ, không đòi hỏi suy nghĩ phức tạp, làm xong trong 2 phút đầu để kích hoạt đà hành động (ví dụ: "Mở sẵn file báo cáo trên máy và gõ dòng tiêu đề", KHÔNG dùng câu mơ hồ như "Bắt đầu nghiên cứu tài liệu").
3. GẮN NHÃN TẢI NHẬN THỨC NGẦM (`cognitive_load`): Đánh giá mức tiêu hao năng lượng nhận thức của từng bước là `"light"`, `"medium"`, hoặc `"heavy"`.
4. RANH GIỚI KIẾN TRÚC: Bạn CHỈ phân rã ngữ nghĩa từ Task sang Steps. TUYỆT ĐỐI KHÔNG tự chia thành các phiên (Sessions), KHÔNG tự cộng/trừ ngày giờ lịch biểu hay nhân hệ số buffer (việc xếp lịch do thuật toán Python đảm nhận 100%)."""

UF6_EMPATHETIC_SYSTEM_PROMPT = """Bạn là người bạn đồng hành ảo (AI Body-Doubling Companion) trên ứng dụng ProPlan VA dành cho người trưởng thành mắc ADHD-I.
Khi lịch làm việc cần điều chỉnh lại (do làm chưa xong, bận đột xuất, bỏ qua phiên hoặc đổi cài đặt), hãy viết:
- `empathetic_note`: ĐÚNG 1 câu giải thích ngắn gọn lý do điều chỉnh lịch kèm lời động viên tinh thần ấm áp, không phán xét (Guilt-free Adaptive Rescheduling), giúp người dùng xóa bỏ hoàn toàn tâm lý tự trách hoặc cảm giác "đằng nào cũng hỏng rồi, bỏ luôn".
- `encouragement_tip`: 1 gợi ý siêu ngắn giúp phục hồi năng lượng nhẹ nhàng."""


class AIService:
    """Điều phối gọi LLM theo kiến trúc Quality-First Warm Backup + Cross-Provider Fallback."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self._gemini_client: genai.Client | None = None
        self._groq_client: AsyncGroq | None = None

    def _get_gemini_client(self) -> genai.Client | None:
        """
        Khởi tạo lười (lazy-init) Gemini Client với retry=1 để lỗi là nhảy tầng tức thì:
        - Ưu tiên 1: File JSON Service Account (`credentials.json`) trên cổng Agent Platform / Vertex Postpay.
        - Ưu tiên 2: Chuỗi `GEMINI_API_KEY` trên cổng Gemini Developer API.
        """
        if self._gemini_client is not None:
            return self._gemini_client

        http_opts = types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1),
        )

        cred_file = self._settings.resolved_google_credentials_file
        if cred_file is not None:
            try:
                raw_meta = json.loads(cred_file.read_text(encoding="utf-8"))
                project_id = (
                    self._settings.GOOGLE_CLOUD_PROJECT.strip()
                    or str(raw_meta.get("project_id", "")).strip()
                )
                if project_id:
                    sa_creds = service_account.Credentials.from_service_account_file(
                        str(cred_file),
                        scopes=_CLOUD_PLATFORM_SCOPES,
                    )
                    self._gemini_client = genai.Client(
                        enterprise=True,
                        credentials=sa_creds,
                        project=project_id,
                        location=self._settings.GOOGLE_CLOUD_LOCATION,
                        http_options=http_opts,
                    )
                    return self._gemini_client
            except Exception as exc:
                logger.warning(
                    "Không thể nạp credentials.json (%s), chuyển sang kiểm tra GEMINI_API_KEY.",
                    type(exc).__name__,
                )

        if self._settings.has_gemini_key:
            self._gemini_client = genai.Client(
                api_key=self._settings.GEMINI_API_KEY,
                http_options=http_opts,
            )
            return self._gemini_client

        return None

    def _get_groq_client(self) -> AsyncGroq | None:
        """Khởi tạo lười (lazy-init) AsyncGroq Client với max_retries=0 để không ngâm lỗi ngầm."""
        if not self._settings.has_groq_key:
            return None
        if self._groq_client is None:
            self._groq_client = AsyncGroq(
                api_key=self._settings.GROQ_API_KEY,
                max_retries=0,
            )
        return self._groq_client

    def _interactive_profile(self) -> ExecutionTimingProfile:
        """Khung thời gian A cho các luồng hội thoại trực tiếp (UF1 & UF6)."""
        hard = self._settings.AI_TIMEOUT_SECONDS
        return ExecutionTimingProfile(
            hard_timeout=hard,
            primary_priority_until=min(hard, self._settings.AI_PRIMARY_PRIORITY_SECONDS),
            warm_backup_at=min(hard, self._settings.AI_WARM_BACKUP_DELAY_SECONDS),
            groq_hedge_at=min(hard, self._settings.AI_GROQ_HEDGE_DELAY_SECONDS),
        )

    def _decomposition_profile(self) -> ExecutionTimingProfile:
        """Khung thời gian B cho luồng bẻ nhỏ công việc có màn hình Breathing Circle (UF2)."""
        hard = self._settings.AI_UF2_TIMEOUT_SECONDS
        return ExecutionTimingProfile(
            hard_timeout=hard,
            primary_priority_until=min(hard, self._settings.AI_UF2_PRIMARY_PRIORITY_SECONDS),
            warm_backup_at=min(hard, self._settings.AI_UF2_WARM_BACKUP_DELAY_SECONDS),
            groq_hedge_at=min(hard, self._settings.AI_UF2_GROQ_HEDGE_DELAY_SECONDS),
        )

    async def _call_gemini_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[SchemaT],
        model_name: str | None = None,
        thinking_level: types.ThinkingLevel = types.ThinkingLevel.LOW,
    ) -> SchemaT:
        """Gọi Gemini đơn lượt (generate_content) với JSON Schema nghiêm ngặt và không lưu trữ trạng thái."""
        client = self._get_gemini_client()
        if client is None:
            raise RuntimeError(
                "Chưa cấu hình chứng thực Gemini (credentials.json hoặc GEMINI_API_KEY)."
            )

        target_model = model_name or self._settings.GEMINI_FLASH_MODEL
        response = await client.aio.models.generate_content(
            model=target_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema_cls,
                thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            ),
        )
        raw_text = getattr(response, "text", None)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError(f"Gemini ({target_model}) trả về nội dung rỗng.")
        return schema_cls.model_validate_json(raw_text)

    async def _call_groq_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """
        Tầng 2: Dự phòng qua Groq Cloud (`qwen/qwen3.8-27b`).
        Nếu model chính trên Groq chạm trần TPM (HTTP 429), tự động chuyển sang model phụ (`openai/gpt-oss-20b`).
        """
        client = self._get_groq_client()
        if client is None:
            raise RuntimeError("GROQ_API_KEY chưa được cấu hình.")

        schema_json = json.dumps(schema_cls.model_json_schema(), ensure_ascii=False)
        strict_system_prompt = (
            f"{system_prompt}\n\n"
            "BẮT BUỘC chỉ trả về duy nhất một đối tượng JSON hợp lệ khớp chính xác với JSON Schema sau "
            f"(không bọc markdown): {schema_json}"
        )

        candidate_models = [self._settings.GROQ_FALLBACK_MODEL.strip()]
        secondary = self._settings.GROQ_SECONDARY_FALLBACK_MODEL.strip()
        if secondary and secondary not in candidate_models:
            candidate_models.append(secondary)

        last_exc: Exception | None = None
        for groq_model in candidate_models:
            if not groq_model:
                continue
            try:
                completion = await client.chat.completions.create(
                    model=groq_model,
                    messages=[
                        {"role": "system", "content": strict_system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                content = completion.choices[0].message.content
                if not content:
                    raise ValueError(f"Groq ({groq_model}) trả về message content rỗng.")
                return schema_cls.model_validate_json(content)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Groq model '%s' thất bại (%s).",
                    groq_model,
                    type(exc).__name__,
                )

        raise last_exc or RuntimeError("Không có model Groq khả dụng.")

    async def _execute_hedged_structured(
        self,
        *,
        flow_name: str,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[SchemaT],
        profile: ExecutionTimingProfile,
    ) -> tuple[SchemaT, GenerationTier]:
        """
        Điều phối thực thi đa tầng ưu tiên chất lượng (Quality-First Hedged Execution):
        1. `0.0s`: Chạy `GEMINI_FLASH_MODEL` (`gemini-3.8-flash`). Nếu xong trước `primary_priority_until` -> nhận ngay.
        2. `warm_backup_at`: Khởi chạy ngầm `GEMINI_BACKUP_MODEL` (`gemini-3.5-flash-lite`) lưu sẵn vào RAM.
           Vẫn kiên nhẫn đợi `3.8-flash` tới mốc `primary_priority_until`, quá mốc này mới bung bản nháp RAM ra trả.
        3. `groq_hedge_at` (hoặc nhảy ngay khi Google lỗi nhanh): Gọi chéo hãng sang Groq nếu chưa có bản dự phòng nào xong.
        4. `hard_timeout`: Ngắt cứng toàn bộ để trả về Tầng 3 (Python Rule-based Fallback).
        """
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        deadline = t0 + profile.hard_timeout
        priority_deadline = min(deadline, t0 + profile.primary_priority_until)

        has_gemini = self._get_gemini_client() is not None
        has_groq = self._get_groq_client() is not None

        # Trường hợp không có chứng thực Gemini -> gọi thẳng sang Groq dưới trần hard_timeout
        if not has_gemini:
            if not has_groq:
                raise RuntimeError(f"{flow_name}: Không có chứng thực Gemini lẫn Groq.")
            groq_only = await asyncio.wait_for(
                self._call_groq_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_cls=schema_cls,
                ),
                timeout=profile.hard_timeout,
            )
            return groq_only, "tier_2_groq"

        primary_model = self._settings.GEMINI_FLASH_MODEL.strip()
        backup_model = self._settings.GEMINI_BACKUP_MODEL.strip()
        can_use_backup = bool(backup_model and backup_model != primary_model)

        primary_task: asyncio.Task[SchemaT] = asyncio.create_task(
            self._call_gemini_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_cls=schema_cls,
                model_name=primary_model,
                thinking_level=types.ThinkingLevel.LOW,
            )
        )
        backup_task: asyncio.Task[SchemaT] | None = None
        groq_task: asyncio.Task[SchemaT] | None = None

        primary_failed = False
        backup_harvested = False
        groq_harvested = False
        warm_result: tuple[SchemaT, GenerationTier] | None = None

        def _start_backup() -> None:
            nonlocal backup_task
            if backup_task is None and can_use_backup:
                backup_task = asyncio.create_task(
                    self._call_gemini_structured(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        schema_cls=schema_cls,
                        model_name=backup_model,
                        thinking_level=types.ThinkingLevel.MINIMAL,
                    )
                )

        def _start_groq() -> None:
            nonlocal groq_task
            if groq_task is None and has_groq:
                groq_task = asyncio.create_task(
                    self._call_groq_structured(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        schema_cls=schema_cls,
                    )
                )

        try:
            while True:
                now = loop.time()

                # 1. Luôn kiểm tra nhánh Primary (3.8-flash) trước tiên để đảm bảo chất lượng cao nhất
                if primary_task.done() and not primary_failed:
                    exc = primary_task.exception()
                    if exc is None:
                        return primary_task.result(), "tier_1_gemini"
                    primary_failed = True
                    logger.warning(
                        "%s Primary (%s) lỗi nhanh (%s), kích hoạt nhánh dự phòng tức thì.",
                        flow_name,
                        primary_model,
                        type(exc).__name__,
                    )
                    if warm_result is not None:
                        return warm_result
                    if can_use_backup:
                        _start_backup()
                    else:
                        _start_groq()

                # 2. Thu hoạch kết quả Warm Backup Gemini (3.5-flash-lite) nếu đã chạy xong
                if backup_task is not None and backup_task.done() and not backup_harvested:
                    backup_harvested = True
                    exc_b = backup_task.exception()
                    if exc_b is None:
                        warm_result = (backup_task.result(), "tier_1_gemini")
                        if primary_failed or now >= priority_deadline:
                            return warm_result
                    else:
                        logger.warning(
                            "%s Warm Backup (%s) thất bại (%s).",
                            flow_name,
                            backup_model,
                            type(exc_b).__name__,
                        )
                        if primary_failed:
                            _start_groq()

                # 3. Thu hoạch kết quả Groq (Tier 2) nếu đã chạy xong
                if groq_task is not None and groq_task.done() and not groq_harvested:
                    groq_harvested = True
                    exc_g = groq_task.exception()
                    if exc_g is None:
                        groq_pair: tuple[SchemaT, GenerationTier] = (
                            groq_task.result(),
                            "tier_2_groq",
                        )
                        all_gemini_failed = primary_failed and (
                            not can_use_backup or backup_harvested
                        )
                        if all_gemini_failed or now >= priority_deadline:
                            return warm_result or groq_pair
                        if warm_result is None:
                            warm_result = groq_pair
                    else:
                        logger.warning(
                            "%s Tier 2 (Groq) thất bại (%s).",
                            flow_name,
                            type(exc_g).__name__,
                        )

                # 4. Nếu đã chạm mốc kết thúc cửa sổ ưu tiên Primary mà trong RAM có sẵn Warm Backup -> trả ngay
                if now >= priority_deadline and warm_result is not None:
                    return warm_result

                # 5. Ngắt cứng tại trần hard_timeout
                if now >= deadline:
                    if warm_result is not None:
                        return warm_result
                    raise TimeoutError(
                        f"{flow_name} vượt trần thời gian xử lý ({profile.hard_timeout}s)."
                    )

                # 6. Kích hoạt theo mốc thời gian cho Warm Backup & Groq Hedge (chỉ gọi Groq nếu chưa có bản RAM)
                if backup_task is None and can_use_backup and now >= t0 + profile.warm_backup_at:
                    _start_backup()
                if (
                    groq_task is None
                    and has_groq
                    and warm_result is None
                    and now >= t0 + profile.groq_hedge_at
                ):
                    _start_groq()

                # 7. Nếu toàn bộ các nhánh có thể chạy đều đã thất bại -> thoát sớm về Tier 3 Rule-based
                backup_exhausted = not can_use_backup or (
                    backup_task is not None and backup_harvested
                )
                groq_exhausted = not has_groq or (groq_task is not None and groq_harvested)
                if primary_failed and backup_exhausted and groq_exhausted and warm_result is None:
                    raise RuntimeError(f"{flow_name}: Toàn bộ các tầng AI đều thất bại.")

                # 8. Tính mốc đánh thức gần nhất để không dùng vòng lặp bận (no busy-polling)
                next_wake = deadline
                if not primary_failed and warm_result is not None and now < priority_deadline:
                    next_wake = min(next_wake, priority_deadline)
                if backup_task is None and can_use_backup:
                    next_wake = min(next_wake, t0 + profile.warm_backup_at)
                if groq_task is None and has_groq and warm_result is None:
                    next_wake = min(next_wake, t0 + profile.groq_hedge_at)

                active_tasks = [
                    t
                    for t in (primary_task, backup_task, groq_task)
                    if t is not None and not t.done()
                ]
                sleep_budget = max(0.0, next_wake - now)
                if active_tasks:
                    await asyncio.wait(
                        active_tasks,
                        timeout=sleep_budget,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    await asyncio.sleep(sleep_budget)
        finally:
            pending = [
                t for t in (primary_task, backup_task, groq_task) if t is not None and not t.done()
            ]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _resolve_current_iso(timezone_name: str, reference_now: datetime | None = None) -> str:
        """Lấy chuỗi thời gian ISO 8601 hiện tại theo múi giờ người dùng để neo ngữ cảnh cho LLM."""
        base_now = reference_now or datetime.now(UTC)
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            tz = UTC
        if base_now.tzinfo is None:
            base_now = base_now.replace(tzinfo=UTC)
        return base_now.astimezone(tz).isoformat()

    # ==========================================================================
    # UF1: TRÍCH XUẤT MỤC TIÊU & GỘP CÂU HỎI LÀM RÕ 1 VÒNG
    # ==========================================================================

    async def extract_task_intent(
        self,
        user_input: str,
        *,
        timezone: str = "Asia/Ho_Chi_Minh",
        busy_intervals: list[dict[str, str]] | None = None,
        reference_now: datetime | None = None,
    ) -> TaskExtractionResult:
        """
        Trích xuất 4 trường bắt buộc ở UF1 theo cơ chế Quality-First Hedged Execution.
        Lưu ý bảo mật: Tuyệt đối không đưa nội dung `user_input` vào log kỹ thuật.
        """
        cleaned_input = user_input.strip()
        if not cleaned_input:
            return self._fallback_extract_task("")

        current_iso = self._resolve_current_iso(timezone, reference_now)
        context_payload: dict[str, Any] = {
            "current_datetime": current_iso,
            "user_timezone": timezone,
            "busy_intervals_from_calendar": busy_intervals or [],
            "user_raw_input": cleaned_input,
        }
        user_prompt = json.dumps(context_payload, ensure_ascii=False)

        try:
            llm_payload, winner_tier = await self._execute_hedged_structured(
                flow_name="UF1",
                system_prompt=UF1_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMTaskExtractionPayload,
                profile=self._interactive_profile(),
            )
            return TaskExtractionResult(
                **llm_payload.model_dump(),
                generation_tier=winner_tier,
            )
        except Exception as exc:
            logger.warning(
                "UF1 chuyển sang Tier 3 Rule-based do lỗi: %s",
                type(exc).__name__,
            )
            return self._fallback_extract_task(cleaned_input)

    def _fallback_extract_task(self, user_input: str) -> TaskExtractionResult:
        """
        Tầng 3 (Rule-based Fallback cho UF1):
        Bảo toàn nguyên vẹn nội dung người dùng vừa nhập, đánh dấu chưa đủ thông tin
        và gộp toàn bộ câu hỏi làm rõ trong 1 lần hiển thị duy nhất (CẤM tự đoán dữ liệu).
        """
        cleaned = user_input.strip()
        short_title = cleaned[:80] if cleaned else None
        content_val = cleaned if cleaned else None

        questions: list[str] = []
        if not content_val:
            questions.append("Bạn muốn thực hiện công việc cụ thể nào?")
        questions.extend(
            [
                "Phạm vi hoặc kết quả đầu ra mong muốn của việc này là gì (ví dụ: số trang, số phần cần hoàn thiện)?",
                "Bạn có những khung giờ rảnh nào để mình sắp xếp các phiên tập trung?",
                "Hạn chót (ngày và giờ) bạn cần hoàn thành xong công việc này là khi nào?",
            ]
        )

        summary_text = (
            f"Đã ghi nhận công việc: '{cleaned}'. Mình cần bạn bổ sung thêm vài thông tin bên dưới để lên lịch chuẩn xác nhé."
            if cleaned
            else "Mình chưa nhận được nội dung công việc rõ ràng, bạn bổ sung giúp mình các thông tin bên dưới nhé."
        )

        return TaskExtractionResult(
            title=short_title,
            content=content_val,
            scope=None,
            free_time_slots=[],
            deadline=None,
            extra_requirements=[],
            is_complete=False,
            has_conflict=False,
            summary=summary_text,
            clarification_questions=questions,
            generation_tier="tier_3_rule_based",
        )

    # ==========================================================================
    # UF2: PHÂN RÃ CÔNG VIỆC CHỦ ĐỘNG & GỢI Ý MICRO-TASK 2 PHÚT
    # ==========================================================================

    async def decompose_task(
        self,
        *,
        task_title: str,
        scope: str | None = None,
        extra_requirements: list[str] | None = None,
        user_focus_duration: int = 25,
    ) -> TaskDecompositionResult:
        """
        Sinh Default Decomposition Proposal (2–5 bước + Micro-task 2 phút) ở UF2
        theo khung thời gian B (trần 6.0s, ưu tiên 3.8-flash tới 5.4s, Warm Backup 3.5-flash-lite từ 3.1s).
        """
        cleaned_title = task_title.strip() or "Công việc cần thực hiện"
        context_payload: dict[str, Any] = {
            "task_title": cleaned_title,
            "scope": scope,
            "extra_requirements": extra_requirements or [],
            "preferred_focus_duration_minutes": user_focus_duration,
        }
        user_prompt = json.dumps(context_payload, ensure_ascii=False)

        try:
            llm_payload, winner_tier = await self._execute_hedged_structured(
                flow_name="UF2",
                system_prompt=UF2_DECOMPOSITION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMTaskDecompositionPayload,
                profile=self._decomposition_profile(),
            )
            return TaskDecompositionResult(
                **llm_payload.model_dump(),
                generation_tier=winner_tier,
            )
        except Exception as exc:
            logger.warning(
                "UF2 chuyển sang Tier 3 Rule-based do lỗi: %s",
                type(exc).__name__,
            )
            return self._fallback_decompose_task(
                cleaned_title,
                user_focus_duration=user_focus_duration,
                scope=scope,
            )

    def _fallback_decompose_task(
        self,
        task_title: str,
        user_focus_duration: int = 25,
        scope: str | None = None,
    ) -> TaskDecompositionResult:
        """
        Tầng 3 (Rule-based Fallback cho UF2):
        Khi mất kết nối tới cả 2 tầng AI, tự động chia thành 3 bước chuẩn theo thời lượng
        tập trung mặc định của người dùng kèm Micro-task khởi động 2 phút cụ thể.
        """
        cleaned_title = task_title.strip() or "Công việc hiện tại"
        safe_duration = max(5, min(180, user_focus_duration))
        scope_hint = f" ({scope.strip()})" if scope and scope.strip() else ""

        steps = [
            StepPromptItem(
                order_index=1,
                title=f"Chuẩn bị tài liệu và lập dàn ý cho: {cleaned_title}",
                expected_output="Danh sách các ý chính hoặc dụng cụ cần thiết đã sẵn sàng.",
                estimated_minutes=safe_duration,
                micro_task_prompt=(
                    f"Dọn gọn mặt bàn trong 2 phút và ghi ra 3 gạch đầu dòng đầu tiên cho '{cleaned_title}'."
                ),
                cognitive_load="light",
            ),
            StepPromptItem(
                order_index=2,
                title=f"Thực hiện phần trọng tâm của: {cleaned_title}{scope_hint}",
                expected_output="Hoàn thành bản nháp hoặc phần khối lượng chính của công việc.",
                estimated_minutes=safe_duration,
                micro_task_prompt=(
                    f"Mở ngay tài liệu/công cụ làm việc của '{cleaned_title}' và bắt tay vào mục đầu tiên."
                ),
                cognitive_load="medium",
            ),
            StepPromptItem(
                order_index=3,
                title=f"Rà soát và hoàn thiện kết quả: {cleaned_title}",
                expected_output="Kết quả đã được kiểm tra lại và sẵn sàng bàn giao/lưu trữ.",
                estimated_minutes=safe_duration,
                micro_task_prompt=(
                    f"Đọc lướt lại phần đầu kết quả của '{cleaned_title}' để kiểm tra các chi tiết còn sót."
                ),
                cognitive_load="light",
            ),
        ]

        return TaskDecompositionResult(
            task_title=cleaned_title,
            steps=steps,
            reasoning=(
                f"Hệ thống đang ở chế độ ngoại tuyến AI nên tự động chia công việc thành 3 bước "
                f"cố định ({safe_duration} phút/bước) để bạn bắt đầu ngay không bị gián đoạn."
            ),
            generation_tier="tier_3_rule_based",
        )

    # ==========================================================================
    # UF6: SINH LỜI GIẢI THÍCH & ĐỘNG VIÊN KHÔNG GÂY ÁP LỰC (GUILT-FREE)
    # ==========================================================================

    async def generate_reschedule_empathy(
        self,
        trigger_reason: str,
        *,
        task_title: str | None = None,
    ) -> RescheduleEmpatheticResult:
        """Sinh câu giải thích dời lịch ấm áp, không phán xét ở UF6 theo cơ chế Hedged Execution."""
        context_payload: dict[str, Any] = {
            "trigger_reason": trigger_reason,
            "task_title": task_title,
        }
        user_prompt = json.dumps(context_payload, ensure_ascii=False)

        try:
            llm_payload, winner_tier = await self._execute_hedged_structured(
                flow_name="UF6",
                system_prompt=UF6_EMPATHETIC_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMRescheduleEmpathyPayload,
                profile=self._interactive_profile(),
            )
            return RescheduleEmpatheticResult(
                **llm_payload.model_dump(),
                generation_tier=winner_tier,
            )
        except Exception as exc:
            logger.warning(
                "UF6 chuyển sang Tier 3 Rule-based do lỗi: %s",
                type(exc).__name__,
            )
            return self._fallback_reschedule_empathy(trigger_reason)

    def _fallback_reschedule_empathy(self, trigger_reason: str) -> RescheduleEmpatheticResult:
        """Tầng 3 (Rule-based Fallback cho UF6): Bộ thông điệp khích lệ Guilt-free chuẩn hóa."""
        templates: dict[str, tuple[str, str]] = {
            "busy": (
                "Có việc đột xuất xen ngang là điều hoàn toàn bình thường trong cuộc sống; mình đã dời phần việc còn lại sang khung giờ rảnh kế tiếp cho bạn rồi nhé.",
                "Hãy giải quyết việc gấp thật thoải mái và uống một ngụm nước trước khi quay lại nhé.",
            ),
            "incomplete": (
                "Bạn đã dành thời gian tập trung rất đáng ghi nhận rồi, phần còn dở mình đã sắp xếp gọn vào khung giờ rảnh tiếp theo để bạn làm tiếp mà không bị áp lực.",
                "Đứng dậy vươn vai và cho mắt nghỉ ngơi vài phút bạn nhé.",
            ),
            "skipped": (
                "Lắng nghe mức năng lượng của bản thân cũng là một kỹ năng quan trọng; mình đã điều chỉnh lại lịch trình sang thời điểm phù hợp hơn cho bạn.",
                "Hít thở sâu 3 nhịp và đừng tự tạo áp lực cho mình nhé.",
            ),
            "setting_changed": (
                "Mình đã cập nhật lại các phiên sắp tới theo đúng nhịp độ làm việc mới mà bạn vừa tùy chỉnh.",
                "Một nhịp làm việc vừa vặn sẽ giúp não bộ duy trì sự tập trung bền bỉ hơn.",
            ),
            "deadline_risk": (
                "Quỹ giờ rảnh hiện tại hơi sát so với hạn chót nên mình đề xuất phương án điều chỉnh nhẹ để lộ trình của bạn vẫn thoải mái và khả thi.",
                "Chúng ta chỉ cần đi từng bước nhỏ một là sẽ về đích đúng hẹn.",
            ),
        }

        note, tip = templates.get(
            trigger_reason,
            (
                "Kế hoạch luôn có thể linh hoạt thích ứng với nhịp sống của bạn; mình đã chuẩn bị sẵn phương án lịch mới nhẹ nhàng hơn bên dưới.",
                "Nghỉ ngơi đôi chút rồi xem qua lịch mới khi bạn sẵn sàng nhé.",
            ),
        )
        return RescheduleEmpatheticResult(
            empathetic_note=note,
            encouragement_tip=tip,
            generation_tier="tier_3_rule_based",
        )


ai_service = AIService()
