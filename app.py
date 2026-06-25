import streamlit as st
import pandas as pd
from pypdf import PdfReader
import io
import re
from datetime import datetime

# 1. Streamlit Seiteneinstellungen (Page Configuration)
st.set_page_config(page_title="DE Beleg-Parser Pro", page_icon="🧾", layout="centered")

st.title("🧾 Automatische Belegabrechnung & MwSt-Rechner")
st.write("Laden Sie Ihre PDF-Rechnungen hier hoch. Das Tool erkennt Rechnungsdatum, Verkäufer, Brutto, Netto, MwSt 19% und benennt die Dateien DATEV-konform um.")

# --- Kernfunktionen für Datenanalyse ---

def extract_text_from_pdf(file_bytes):
    """Extrahiert Text aus hochgeladenen PDF-Dateien"""
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception:
        return ""

def advanced_date_parser(text):
    """Ermittelt das korrekte Rechnungsdatum anhand von Schlüsselwörtern"""
    text_lines = text.split('\n')
    date_keywords = ["rechnungsdatum", "leistungsdatum", "belegdatum", "datum vom", "datum:", "ausstellungsdatum"]
    
    # 1. Priorität: Datum in der gleichen Zeile wie ein Schlüsselwort
    for line in text_lines:
        line_low = line.lower()
        if any(kw in line_low for kw in date_keywords):
            match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", line)
            if match:
                return match.group(1)

    # 2. Priorität: Das erste gefundene Datum im gesamten Dokument
    all_dates = re.findall(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    if all_dates:
        return all_dates[0]
        
    return datetime.now().strftime("%Y-%m-%d")

def advanced_vendor_parser(text):
    """Ermittelt den Verkäufer mittels Fix-Keywords, Kontext-Regeln oder Adress-Scoring"""
    
    # [A] Fix-Keywords (Robuste Suche durch komplette Leerzeichenentfernung)
    clean_text = re.sub(r'\s+', '', text.lower())
    
    if "amazon" in clean_text: return "Amazon"
    elif "tesla" in clean_text or "supercharger" in clean_text: return "Tesla"
    elif "santander" in clean_text: return "Santander"
    elif any(kw in clean_text for kw in ["stadtmobil", "rheinruhr", "rhein-ruhr"]): return "Stadtmobil"
    elif "dpd" in clean_text: return "DPD"
    elif "flaschenpost" in clean_text: return "Flaschenpost"
    elif any(kw in clean_text for kw in ["shell", "aral", "totalenergies"]): return "Tankstelle"

    # [B] Kontext-Regeln (z.B. "Verkauft von WEPA eCommerce GmbH")
    # 1. "Verkauft von" Muster
    context_match1 = re.search(r"verkauft\s+von\s+([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG|Inc|Ltd|SE)?)", text, re.IGNORECASE)
    if context_match1:
        vendor_name = context_match1.group(1).strip().split('\n')[0].strip()
        return re.sub(r'[:;,]+$', '', vendor_name).strip()

    # 2. "Rechnung von" Muster
    context_match2 = re.search(r"rechnung\s+von\s+([A-Za-z0-9\s\.\&\-\_]+(GmbH|AG|GbR|KG|SE)?)", text, re.IGNORECASE)
    if context_match2:
        vendor_name = context_match2.group(1).strip().split('\n')[0].strip()
        return re.sub(r'[:;,]+$', '', vendor_name).strip()

    # [C] Adress-Scoring (Sucht nach PLZ + Stadt und analysiert die Zeilen darüber)
    lines = text.split('\n')
    for i, line in enumerate(lines):
        plz_match = re.search(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß]+", line)
        if plz_match:
            candidates = []
            if i > 0: candidates.append(lines[i-1].strip())
            if i > 1: candidates.append(lines[i-2].strip())
            candidates.append(line.strip())
            
            # Prio 1: Zeile enthält Rechtsform (GmbH, SE, AG, KG) -> Höchste Wahrscheinlichkeit für Firmennamen
            for cand in candidates:
                cand_low = cand.lower()
                if "gmbh" in cand_low or "ag" in cand_low or "kg" in cand_low or re.search(r"\bse\b", cand_low):
                    company_match = re.search(r"([A-Za-z0-9\&\-\_\s]+(?:GmbH|AG|GbR|KG|SE))", cand)
                    if company_match:
                        return company_match.group(1).strip()
                    return cand

            # Prio 2: Keine Rechtsform da, nimm die Zeile darüber, schließe aber reine Straßennamen aus
            if i > 0 and len(lines[i-1].strip()) > 2:
                potential_vendor = lines[i-1].strip()
                if re.search(r"(str|weg|straße|platz)\b", potential_vendor.lower()) and i > 1:
                    potential_vendor = lines[i-2].strip()
                
                if len(potential_vendor) < 40:
                    return potential_vendor

    return "Unbekannt"

def parse_financial_amounts(text):
    """Extrahiert Bruttobetrag und berechnet/extrahiert 19% MwSt"""
    text_lower = text.lower()
    total_amount = 0.0
    mwst_19 = 0.0

    # 1. Strikte MwSt-Isolierung vorab
    mwst_match = re.search(r"(19%\s*(mwst|ust|mehrwertsteuer)|(mwst|ust)\s*19%)\s*:?\s*([\d\.]*,\d{2})", text_lower)
    if mwst_match:
        try:
            mwst_19 = float(mwst_match.group(4).replace(".", "").replace(",", "."))
        except:
            pass

    # 2. Brutto extrahieren (Zeilen ohne "mwst"/"netto" bevorzugen)
    lines = text.split('\n')
    for line in reversed(lines):
        line_low = line.lower()
        if any(k in line_low for k in ["total", "gesamtsumme", "endbetrag", "brutto", "rechnungsbetrag", "zu zahlen"]):
            if "mwst" in line_low or "netto" in line_low or "ust" in line_low:
                continue
            
            price_match = re.search(r"([\d\.]*,\d{2})", line)
            if price_match:
                try:
                    total_amount = float(price_match.group(1).replace(".", "").replace(",", "."))
                    break
                except:
                    continue

    # 3. Logische Plausibilitätsprüfungen und Kreuzrechnungen
    if total_amount == 0.0 and mwst_19 > 0:
        total_amount = round(mwst_19 * 119 / 19, 2)
    elif mwst_19 == 0.0 and total_amount > 0 and "19%" in text_lower:
        mwst_19 = round(total_amount * 19 / 119, 2)

    return total_amount, mwst_19

# --- UI Implementierung (Streamlit Frontend) ---

uploaded_files = st.file_uploader("Wählen Sie PDF-Rechnungen aus (Mehrfachauswahl möglich)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    
    st.subheader("Analyse-Protokoll")
    
    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        raw_text = extract_text_from_pdf(file_bytes)
        
        # Daten-Parsing über das Kombi-Modul
        detected_date = advanced_date_parser(raw_text)
        vendor = advanced_vendor_parser(raw_text)
        total, mwst_19 = parse_financial_amounts(raw_text)
        
        # ISO-Konvertierung des Datums (DD.MM.YYYY zu YYYY-MM-DD)
        date_str = detected_date
        if "." in detected_date:
            try:
                date_str = datetime.strptime(detected_date, "%d.%m.%Y").strftime("%Y-%m-%d")
            except:
                pass
        
        # 특수문자나 공백이 파일명에 악영향을 주지 않도록 가볍게 정제
        vendor_clean = re.sub(r'[\\/*?:"<>|]', '', vendor).strip()
        
        # DATEV-konformer Dateiname: YYYY-MM-DD_Verkäufer_BetragEUR.pdf
        proposed_name = f"{date_str}_{vendor_clean}_{total:.2f}EUR.pdf"
        
        st.success(f"✔ Erfolgreich: {uploaded_file.name} ➔ **{proposed_name}**")
        
        receipt_data.append({
            "Rechnungsdatum": date_str,
            "Verkäufer": vendor,
            "Brutto (€)": total,
            "MwSt 19% (€)": mwst_19,
            "Netto (€)": round(total - mwst_19, 2),
            "DATEV-Dateiname": proposed_name
        })
        
    # Zusammenfassung und Tabellenanzeige
    df = pd.DataFrame(receipt_data)
    
    st.markdown("---")
    st.subheader("📊 Monatliche Auswertungsübersicht")
    
    total_brutto = df["Brutto (€)"].sum()
    total_mwst = df["MwSt 19% (€)"].sum()
    total_netto = df["Netto (€)"].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Gesamt Brutto", f"{total_brutto:,.2f} €")
    col2.metric("Erstattbare MwSt (19%)", f"{total_mwst:,.2f} €")
    col3.metric("Gesamt Netto", f"{total_netto:,.2f} €")
    
    st.dataframe(df, use_container_width=True)
    
    # Excel-Export mit Summenzeile vorbereiten
    total_row = {
        "Rechnungsdatum": "GESAMT (Total)", 
        "Verkäufer": "", 
        "Brutto (€)": total_brutto, 
        "MwSt 19% (€)": total_mwst, 
        "Netto (€)": total_netto, 
        "DATEV-Dateiname": f"{len(df)} Belege"
    }
    df_excel = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_excel.to_excel(writer, index=False, sheet_name='Ausgaben')
    processed_data = output.getvalue()
    
    current_month = datetime.now().strftime("%Y-%m")
    st.download_button(
        label="📥 Monatsbericht als Excel (.xlsx) herunterladen",
        data=processed_data,
        file_name=f"Ausgabenbericht_{current_month}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
