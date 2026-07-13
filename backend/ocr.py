import os
from typing import Literal
from pydantic import BaseModel, Field
import google.generativeai as genai

GEMINI_MODEL = "gemini-3.1-flash-lite"

# 💡 [3단계 핵심] AI가 무조건 이 규격의 JSON/오브젝트로 대답하도록 강제하는 스키마
class StructuredReceiptResponse(BaseModel):
    beleg_nr: str = Field(description="Die Rechnungs- oder Belegnummer. Falls nicht vorhanden, 'None'.")
    datum: str = Field(description="Das Rechnungsdatum strictly im Format YYYY-MM-DD.")
    vendor: str = Field(description="Der offizielle Name des Ausstellers (max 12 Zeichen).")
    total: float = Field(description="Der Bruttobetrag als reine Fließkommazahl mit Punkt.")
    currency: Literal["EUR", "USD"] = Field(description="Die offizielle Währung.")
    mwst_type: Literal["19_Only", "7_Only", "Split", "0_Only", "AUTO_19"] = Field(description="Klassifizierung des Steuersatzes.")

def configure_gemini(api_key: str):
    if api_key:
        genai.configure(api_key=api_key)

def load_prompt(prompt_name: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(base_dir, "prompts", prompt_name)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Extrahiere Rechnungsdaten strukturiert."

def ask_gemini_structured(file_bytes: bytes, mime_type: str, api_key: str) -> StructuredReceiptResponse:
    """텍스트가 아닌 완벽한 타입 안전성을 가진 Pydantic 객체를 반환합니다."""
    configure_gemini(api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = load_prompt("receipt_prompt.txt")
    
    # 🌟 Gemini Engine에 Structured Output 탑재 요청 선언
    response = model.generate_content(
        [{"mime_type": mime_type, "data": file_bytes}, prompt],
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": StructuredReceiptResponse,
        }
    )
    
    # Pydantic을 활용하여 안전하게 검증 및 JSON 파싱 처리
    return StructuredReceiptResponse.model_validate_json(response.text)
