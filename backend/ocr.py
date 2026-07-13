import os
import re
from datetime import datetime
import google.generativeai as genai

GEMINI_MODEL = "gemini-3.1-flash-lite"

def configure_gemini(api_key: str):
    """Gemini API 인증 및 설정"""
    if api_key:
        genai.configure(api_key=api_key)

def load_prompt(prompt_name: str) -> str:
    """
    📁 prompts/ 폴더에서 지정된 .txt 프롬프트 파일을 읽어오는 함수
    app.py 기준 최상위 경로의 prompts/ 폴더를 안전하게 역추적하여 로드합니다.
    """
    # 현재 파일(backend/ocr.py)의 상위 폴더(최상위 루트) 경로 획득
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(base_dir, "prompts", prompt_name)
    
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # 혹시 파일이 없을 경우를 대비한 하드코딩 백업 규칙 (안정성 확보)
        return """Du bist ein Experte für deutsche Finanzbuchhaltung. Extrahiere folgende Daten:
Beleg_Nr, Datum(YYYY-MM-DD), Vendor(max 12 chars), Total(Zahl mit .), Currency(EUR/USD), Kategorie(AUTO), MwSt_Type.
Ausgabe strictly 7 Zeilen."""

def parse_german_amount(raw: str) -> float:
    """독일식 금액 표기법(,와 .의 혼용)을 파이썬 float형으로 정규화"""
    s = re.sub(r"[€$£\s]", "", raw)
    s = re.sub(r"(?i)(eur|usd|gbp)", "", s).strip()
    if not s: return 0.0
    if "." in s and "," in s:
        if s.rfind(".") > s.rfind(","): s = s.replace(",", "")
        else: s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        if len(s[s.rfind(",") + 1:]) == 2: s = s.replace(",", ".")
        else: s = s.replace(",", "")
    try: return float(s)
    except ValueError: return 0.0

def parse_gemini_response(text: str) -> tuple:
    """Gemini가 7줄 구조로 뱉은 텍스트 응답을 파이썬 튜플 데이터로 구조화 파싱"""
    beleg_nr, date_str, vendor, total, currency = "", datetime.now().strftime("%Y-%m-%d"), "Unbekannt", 0.0, "EUR"
    kategorie, mwst_type = "", "AUTO_19"
    cleaned = re.sub(r"[*`]", "", text)
    for line in cleaned.splitlines():
        if ":" not in line: continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        match key:
            case "Beleg_Nr": beleg_nr = value
            case "Datum":
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): date_str = value
            case "Vendor": 
                if value: vendor = value
            case "Total": total = parse_german_amount(value)
            case "Currency":
                if value.upper() in ["EUR", "USD"]: currency = value.upper()
            case "MwSt_Type":
                if value: mwst_type = value
    return beleg_nr, date_str, vendor, total, currency, kategorie, mwst_type
