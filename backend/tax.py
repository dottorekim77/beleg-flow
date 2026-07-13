MWST_19_FACTOR = 19 / 119
MWST_7_FACTOR  = 7 / 107

def calculate_tax_details(brutto_eur: float, mwst_type: str) -> tuple[float, float, float]:
    """Brutto 금액과 Steuerschlüssel 기준 USt 19%, Vorsteuer 7%, Netto 금액 산출"""
    mwst_19, mwst_7 = 0.0, 0.0
    if mwst_type in ("19_Only", "AUTO_19"): 
        mwst_19 = round(brutto_eur * MWST_19_FACTOR, 2)
    elif mwst_type == "7_Only": 
        mwst_7 = round(brutto_eur * MWST_7_FACTOR, 2)
    elif mwst_type == "Split":
        half = round(brutto_eur / 2, 2)
        mwst_19 = round(half * MWST_19_FACTOR, 2)
        mwst_7 = round((brutto_eur - half) * MWST_7_FACTOR, 2)
        
    netto = round(brutto_eur - (mwst_19 + mwst_7), 2)
    return mwst_19, mwst_7, netto
