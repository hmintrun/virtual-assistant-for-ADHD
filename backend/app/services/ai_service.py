"""Dịch vụ tích hợp LLM 3 tầng: Gemini 3.8 Flash (Tier 1) -> Groq Llama-3 (Tier 2) -> Rule-based (Tier 3)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google import genai
from groq import AsyncGroq
from pydantic import BaseModel

from app.core.config import Settings, settings
from app.schemas.schedule import (
    LLMRescheduleEmpathyPayload,
    RescheduleEmpatheticResult,
)
from app.schemas.task import (
    LLMTaskDecompositionPayload,
    LLMTaskExtractionPayload,
    StepPromptItem,
    TaskDecompositionResult,
    TaskExtractionResult,
)

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

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
    """Điều phối gọi LLM theo kiến trúc dự phòng 3 tầng với giới hạn độ trễ <= 2.5s."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self._gemini_client: genai.Client | None = None
        self._groq_client: AsyncGroq | None = None

    def _get_gemini_client(self) -> genai.Client | None:
        """Khởi tạo lười (lazy-init) Gemini Client khi có GEMINI_API_KEY hợp lệ."""
        if not self._settings.has_gemini_key:
            return None
        if self._gemini_client is None:
            self._gemini_client = genai.Client(api_key=self._settings.GEMINI_API_KEY)
        return self._gemini_client

    def _get_groq_client(self) -> AsyncGroq | None:
        """Khởi tạo lười (lazy-init) AsyncGroq Client cho Tầng 2 khi có GROQ_API_KEY hợp lệ."""
        if not self._settings.has_groq_key:
            return None
        if self._groq_client is None:
            self._groq_client = AsyncGroq(api_key=self._settings.GROQ_API_KEY)
        return self._groq_client

    async def _call_gemini_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """Tầng 1: Gọi Gemini 3.8 Flash qua Interactions API với JSON Schema chặt chẽ."""
        client = self._get_gemini_client()
        if client is None:
            raise RuntimeError("GEMINI_API_KEY chưa được cấu hình.")

        async def _invoke() -> SchemaT:
            interaction = await client.aio.interactions.create(
                model=self._settings.GEMINI_FLASH_MODEL,
                system_instruction=system_prompt,
                input=user_prompt,
                store=False,  # Bảo mật quyền riêng tư: không lưu trữ nội dung của người dùng trên cloud
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema_cls.model_json_schema(),
                },
            )
            raw_text = getattr(interaction, "output_text", None)
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise ValueError("Gemini trả về output_text rỗng.")
            return schema_cls.model_validate_json(raw_text)

        return await asyncio.wait_for(_invoke(), timeout=self._settings.AI_TIMEOUT_SECONDS)

    async def _call_groq_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[SchemaT],
    ) -> SchemaT:
        """Tầng 2: Dự phòng qua Groq Cloud (Llama-3) khi Gemini timeout hoặc lỗi."""
        client = self._get_groq_client()
        if client is None:
            raise RuntimeError("GROQ_API_KEY chưa được cấu hình.")

        schema_json = json.dumps(schema_cls.model_json_schema(), ensure_ascii=False)
        strict_system_prompt = (
            f"{system_prompt}\n\n"
            "BẮT BUỘC chỉ trả về duy nhất một đối tượng JSON hợp lệ khớp chính xác với JSON Schema sau "
            f"(không bọc markdown): {schema_json}"
        )

        async def _invoke() -> SchemaT:
            completion = await client.chat.completions.create(
                model=self._settings.GROQ_FALLBACK_MODEL,
                messages=[
                    {"role": "system", "content": strict_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = completion.choices[0].message.content
            if not content:
                raise ValueError("Groq trả về message content rỗng.")
            return schema_cls.model_validate_json(content)

        return await asyncio.wait_for(_invoke(), timeout=self._settings.AI_TIMEOUT_SECONDS)

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
        Trích xuất 4 trường bắt buộc ở UF1 theo cơ chế 3-Tier Graceful Degradation.
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

        # Tầng 1: Gemini 3.8 Flash
        try:
            llm_payload = await self._call_gemini_structured(
                system_prompt=UF1_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMTaskExtractionPayload,
            )
            return TaskExtractionResult(
                **llm_payload.model_dump(),
                generation_tier="tier_1_gemini",
            )
        except Exception as exc:
            logger.warning(
                "UF1 Tier 1 (Gemini) chuyển sang Tier 2 do lỗi: %s",
                type(exc).__name__,
            )

        # Tầng 2: Groq Llama-3 Fallback
        try:
            llm_payload = await self._call_groq_structured(
                system_prompt=UF1_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMTaskExtractionPayload,
            )
            return TaskExtractionResult(
                **llm_payload.model_dump(),
                generation_tier="tier_2_groq",
            )
        except Exception as exc:
            logger.warning(
                "UF1 Tier 2 (Groq) chuyển sang Tier 3 Rule-based do lỗi: %s",
                type(exc).__name__,
            )

        # Tầng 3: Deterministic Rule-based Fallback
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
        theo cơ chế dự phòng 3 tầng.
        """
        cleaned_title = task_title.strip() or "Công việc cần thực hiện"
        context_payload: dict[str, Any] = {
            "task_title": cleaned_title,
            "scope": scope,
            "extra_requirements": extra_requirements or [],
            "preferred_focus_duration_minutes": user_focus_duration,
        }
        user_prompt = json.dumps(context_payload, ensure_ascii=False)

        # Tầng 1: Gemini 3.8 Flash
        try:
            llm_payload = await self._call_gemini_structured(
                system_prompt=UF2_DECOMPOSITION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMTaskDecompositionPayload,
            )
            return TaskDecompositionResult(
                **llm_payload.model_dump(),
                generation_tier="tier_1_gemini",
            )
        except Exception as exc:
            logger.warning(
                "UF2 Tier 1 (Gemini) chuyển sang Tier 2 do lỗi: %s",
                type(exc).__name__,
            )

        # Tầng 2: Groq Llama-3 Fallback
        try:
            llm_payload = await self._call_groq_structured(
                system_prompt=UF2_DECOMPOSITION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMTaskDecompositionPayload,
            )
            return TaskDecompositionResult(
                **llm_payload.model_dump(),
                generation_tier="tier_2_groq",
            )
        except Exception as exc:
            logger.warning(
                "UF2 Tier 2 (Groq) chuyển sang Tier 3 Rule-based do lỗi: %s",
                type(exc).__name__,
            )

        # Tầng 3: Deterministic Rule-based Fallback
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
        """Sinh câu giải thích dời lịch ấm áp, không phán xét ở UF6 theo 3 tầng dự phòng."""
        context_payload: dict[str, Any] = {
            "trigger_reason": trigger_reason,
            "task_title": task_title,
        }
        user_prompt = json.dumps(context_payload, ensure_ascii=False)

        # Tầng 1: Gemini 3.8 Flash
        try:
            llm_payload = await self._call_gemini_structured(
                system_prompt=UF6_EMPATHETIC_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMRescheduleEmpathyPayload,
            )
            return RescheduleEmpatheticResult(
                **llm_payload.model_dump(),
                generation_tier="tier_1_gemini",
            )
        except Exception as exc:
            logger.warning(
                "UF6 Tier 1 (Gemini) chuyển sang Tier 2 do lỗi: %s",
                type(exc).__name__,
            )

        # Tầng 2: Groq Llama-3 Fallback
        try:
            llm_payload = await self._call_groq_structured(
                system_prompt=UF6_EMPATHETIC_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=LLMRescheduleEmpathyPayload,
            )
            return RescheduleEmpatheticResult(
                **llm_payload.model_dump(),
                generation_tier="tier_2_groq",
            )
        except Exception as exc:
            logger.warning(
                "UF6 Tier 2 (Groq) chuyển sang Tier 3 Rule-based do lỗi: %s",
                type(exc).__name__,
            )

        # Tầng 3: Deterministic Rule-based Fallback
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
