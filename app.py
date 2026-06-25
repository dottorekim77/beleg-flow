import os
import re
from datetime import datetime
import io
import pandas as pd
from pypdf import PdfReader
import streamlit as st

# 1. Streamlit Seiteneinstellungen (Page Configuration)
st.set_page_config(page_title="DE Beleg-Parser", page_icon="🧾", layout="centered")

# UI auf Deutsch
st.title("🧾 Automatische Belegabrechnung & MwSt-Rechner")
st.write("Laden Sie Ihre PDF-Rechnungen hier hoch. Das Tool erkennt Rechnungsdatum, Brutto, Netto, MwSt 19% und benennt die Dateien DATEV-konform um.")

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

def parse_receipt_info(text):
    """Extrahiert Rechnungsdatum, Verkäufer, Bruttobetrag und 19% MwSt aus dem Text"""
    
    # 1. Verkäufer (Händler) erkennen - Groß-/Kleinschreibung ignorieren (Flexible Suche)
    vendor = "Unbekannt"
    text_lower = text.lower()
    
    if "amazon" in text_lower:
        vendor = "Amazon"
    elif "tesla" in text_lower or "supercharger" in text_lower:
        vendor = "Tesla"
    elif "santander" in text_lower:
        vendor = "Santander"
    elif "stadtmobil" in text_lower or "rhein-ruhr" in text_lower:
        vendor = "Stadtmobil"
    elif "shell" in text_lower or "totalenergies" in text_lower or "aral" in text_lower:
        vendor = "Tankstelle" # Tankstellen als Bonus hinzugefügt
        
    # 2. Rechnungsdatum extrahieren (DD.MM.YYYY oder YYYY-MM-DD)
    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", text)
    date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")
    
    # Konvertierung von DD.MM.YYYY zu YYYY-MM-DD für DATEV
    if "." in date_str:
        try:
            date_str = datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
        except:
            pass

    # 3. Bruttobetrag extrahieren (Unterstützt deutsche Formatierung wie 1.234,56 또는 123,45)
    total_amount = 0.0
    # Sucht nach typischen deutschen Rechnungsbegriffen
    total_patterns = [
        r"(Total|Gesamtsumme|Endbetrag|Bruttobetrag|Rechnungsbetrag|Zu zahlen|EUR|€)\s*:?\s*([\d\.]*,\d{2})",
        r"([\d\.]*,\d{2})\s*(EUR|€|Gesamt)"
    ]
    
    for pattern in total_patterns:
        total_match = re.search(pattern, text, re.IGNORECASE)
        if total_match:
            # Wenn das Pattern zwei Gruppen hat, nehmen wir die mit den Zahlen
            match_str = total_match.group(2) if "," in total_match.group(2) else total_match.group(1)
            try:
                # Deutsche Formatierung (Tausendertrennpunkt entfernen, Komma durch Punkt ersetzen)
                clean_str = match_str.replace(".", "").replace(",", ".")
                total_amount = float(clean_str)
                break
            except:
                continue

    # 4. 19% MwSt extrahieren oder berechnen
    mwst_19 = 0.0
    # Sucht nach "19% MwSt", "inkl. 19%", "USt 19%" etc.
    mwst_patterns = [
        r"(19%\s*(MwSt|USt|Mehrwertsteuer|Mehrwertst\.|Vat)|(MwSt|USt|Mehrwertsteuer)\s*19%)\s*:?\s*([\d\.]*,\d{2})",
        r"([\d\.]*,\d{2})\s*(Zgsl\.\s*)?19%\s*(MwSt|USt)"
    ]
    
    for pattern in mwst_patterns:
        mwst_match = re.search(pattern, text, re.IGNORECASE)
        if mwst_match:
            match_str = mwst_match.group(4) if len(mwst_match.groups()) >= 4 and mwst_match.group(4) else mwst_match.group(1)
            try:
                clean_str = match_str.replace(".", "").replace(",", ".")
                mwst_19 = float(clean_str)
                break
            except:
                continue
                
    # Falls im Text "19%" steht, aber kein konkreter MwSt-Betrag gefunden wurde -> Rückrechnung aus Brutto
    if mwst_19 == 0.0 and "19%" in text and total_amount > 0:
        mwst_19 = round(total_amount * 19 / 119, 2)

    return date_str, vendor, total_amount, mwst_19

# --- UI Implementierung ---

# 2. Drag & Drop File Uploader
uploaded_files = st.file_uploader("Wählen Sie PDF-Rechnungen aus (Mehrfachauswahl möglich)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    receipt_data = []
    
    st.subheader("Analyse-Protokoll")
    
    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        text = extract_text_from_pdf(file_bytes)
        date_str, vendor, total, mwst_19 = parse_receipt_info(text)
        
        # DATEV-konformer Dateiname: YYYY-MM-DD_Verkäufer_BetragEUR.pdf
        proposed_name = f"{date_str}_{vendor}_{total:.2f}EUR.pdf"
        
        st.success(f"✔ Erfolgreich: {uploaded_file.name} ➔ **{proposed_name}**")
        
        receipt_data.append({
            "Rechnungsdatum": date_str,
            "Verkäufer": vendor,
            "Brutto (€)": total,
            "MwSt 19% (€)": mwst_19,
            "Netto (€)": round(total - mwst_19, 2),
            "DATEV-Dateiname": proposed_name
        })
        
    # 3. Zusammenfassung und Tabellenanzeige
    df = pd.DataFrame(receipt_data)
    
    st.markdown("---")
    st.subheader("📊 Monatliche Auswertungsübersicht")
    
    total_brutto = df["Brutto (€)"].sum()
    total_mwst = df["MwSt 19% (€)"].sum()
    total_netto = df["Netto (€)"].sum()
    
    # Metriken im Dashboard-Stil
    col1, col2, col3 = st.columns(3)
    col1.metric("Gesamt Brutto", f"{total_brutto:,.2f} €")
    col2.metric("Erstattbare MwSt (19%)", f"{total_mwst:,.2f} €")
    col3.metric("Gesamt Netto", f"{total_netto:,.2f} €")
    
    # Tabelle anzeigen
    st.dataframe(df, use_container_width=True)
    
    # 4. Excel-Export mit Summenzeile
    total_row = {
        "Rechnungsdatum": "GESAMT (Total)", 
        "Verkäufer": "", 
        "Brutto (€)": total_brutto, 
        "MwSt 19% (€)": total_mwst, 
        "Netto (€)": total_netto, 
        "DATEV-Dateiname": f"{len(df)} Belege"
    }
    df_excel = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
    
    # Excel im Speicher generieren
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
