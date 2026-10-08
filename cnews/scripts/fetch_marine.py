"""
fetch_marine.py
Récupère en temps réel (Token 0 / sans copier-coller) les bulletins officiels
de Météo-France pour la météo marine côtière (BMR & BMS).
Source : https://meteofrance.com/meteo-marine/{zone_slug}/{domain_id}
"""
import urllib.request
import urllib.parse
import re
import xml.etree.ElementTree as ET
import json
import datetime

# ponytail: simple dictionary mapping radio/client names to Météo-France coastal marine domains
MARINE_DOMAINS = {
    # Frontière belge à Baie de Somme (Dunkerque, Calais, Boulogne, Le Touquet, Berck)
    "BMSCOTE-01-01": "BMSCOTE-01-01",
    "NORD": "BMSCOTE-01-01",
    "RADIO 6": "BMSCOTE-01-01",
    "MONA": "BMSCOTE-01-01",
    "HDF": "BMSCOTE-01-01",
    # Baie de Somme à La Hague (Dieppe, Fécamp, Le Havre, Deauville, Cherbourg)
    "NORMANDIE": "BMSCOTE-01-02",
    # Penmarc'h à Aiguillon (Vendée, Loire-Atlantique)
    "PDL": "BMSCOTE-01-04",
    # Aiguillon à Frontière espagnole (La Rochelle, Royan, Charente-Maritime, Gironde)
    "ROCHELLE": "BMSCOTE-01-05",
    "NAQ": "BMSCOTE-01-05",
}

def get_mf_token():
    """Récupère le jeton anonyme de session Météo-France via le cookie mfsession (rot13)."""
    try:
        req = urllib.request.Request('https://meteofrance.com', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            for c in resp.headers.get_all('Set-Cookie') or []:
                m = re.search(r'mfsession=([^;]+)', c)
                if m:
                    s = urllib.parse.unquote(m.group(1))
                    return s.translate(str.maketrans(
                        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',
                        'NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm'
                    ))
    except Exception as e:
        print(f"[fetch_marine] Erreur token Météo-France: {e}")
    return None

def fetch_marine_data(domain_id="BMSCOTE-01-01", day_offset=1):
    """Interroge l'API Météo-France pour extraire le BMR et l'éventuel BMS."""
    token = get_mf_token()
    if not token:
        return None, None
        
    bmr_id = domain_id.replace("BMS", "BMR")
    bmr_url = (
        f"https://rwg.meteofrance.com/internet2018client/2.0/report?"
        f"domain={bmr_id}&report_type=marine&report_subtype=BMR_cote_fr&format=xml&token={token}"
    )
    
    bmr_data = {}
    try:
        req = urllib.request.Request(bmr_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read().decode('utf-8'))
            bmr_data['titre'] = root.findtext('titreBulletin', '').strip()
            bmr_data['chapeau'] = root.findtext('chapeauBulletin', '').strip()
            bmr_data['bms_ref'] = root.findtext('bulletinSpecial', '').strip()
            
            for ech in root.findall('echeance'):
                id_ech = ech.get('idEcheance', '')
                reg = ech.find('region')
                if reg is not None:
                    bmr_data[id_ech] = {
                        'titre': ech.findtext('titreEcheance', '').strip(),
                        'situation': reg.findtext('situation', '').strip(),
                        'observation': reg.findtext('observation', '').strip(),
                        'vent': reg.findtext('vent', '').strip(),
                        'mer': reg.findtext('mer', '').strip(),
                        'houle': reg.findtext('houle', '').strip(),
                        'temps': reg.findtext('TS', '').strip(),
                        'visibilite': reg.findtext('visi', '').strip(),
                    }
    except Exception as e:
        print(f"[fetch_marine] Erreur lecture BMR XML: {e}")
        return None, None

    # Extraction BMS (avis de sécurité / coup de vent)
    bms_url = (
        f"https://rwg.meteofrance.com/internet2018client/2.0/report?"
        f"domain={domain_id}&report_type=marine&report_subtype=BMS_cote_fr&format=json&token={token}"
    )
    bms_items = []
    try:
        req_bms = urllib.request.Request(bms_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_bms, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for bloc in data.get('text_bloc_item', []):
                for item in bloc.get('text_items', []):
                    txt = item.get('text') or item.get('title') or ''
                    if txt:
                        bms_items.append(txt.strip())
    except Exception:
        pass

    return bmr_data, bms_items

def get_marine_text_for_client(client_name="RADIO 6", day_offset=1, date_str=""):
    """Génère le texte complet de la météo marine pour un client radio donné."""
    # Détection du domaine marin
    domain_id = "BMSCOTE-01-01"
    for k, v in MARINE_DOMAINS.items():
        if k in client_name.upper():
            domain_id = v
            break
            
    bmr_data, bms_items = fetch_marine_data(domain_id, day_offset)
    if not bmr_data:
        return ""

    lines = []
    zone_label = "FRONTIÈRE BELGE / BAIE DE SOMME" if domain_id == "BMSCOTE-01-01" else bmr_data.get('titre', 'LITTORAL')
    lines.append(f"🌊 MÉTÉO MARINE MÉTÉO-FRANCE – {zone_label.upper()}" + (f" ({date_str})" if date_str else ""))
    
    if domain_id == "BMSCOTE-01-01":
        lines.append("📍 Bande des 20 milles : Dunkerque • Calais • Boulogne • Le Touquet • Baie de Somme\n")
    else:
        lines.append(f"📍 {bmr_data.get('chapeau', '')}\n")

    # BMS éventuel
    if bms_items:
        # Nettoyage et synthèse de l'avis de sécurité
        avis = [it for it in bms_items if "Avis" in it or "VENT :" in it or "Reprise" in it or "En cours" in it]
        if avis:
            lines.append("⚠️ AVIS DE SÉCURITÉ EN MER (BMS OFFICIEL) :")
            for a in avis[:4]:
                lines.append(f"• {a}")
            lines.append("")
    elif bmr_data.get('bms_ref'):
        lines.append(f"⚠️ AVIS DE SÉCURITÉ : {bmr_data.get('bms_ref')}\n")

    # Situation synoptique
    sit = bmr_data.get('sit1', {}).get('situation')
    if sit:
        lines.append(f"🌐 Situation générale : {sit}\n")

    # Observations réelles si jour même
    if day_offset == 0 and bmr_data.get('obs', {}).get('observation'):
        obs_text = bmr_data['obs']['observation'].replace('\n', ' • ')
        lines.append(f"⚓ Dernières observations : {obs_text}\n")

    # Prévision pour le jour cible
    ech_key = f"j{day_offset}" if f"j{day_offset}" in bmr_data else "j1"
    fc = bmr_data.get(ech_key, {})
    if fc:
        titre_fc = fc.get('titre', f'Prévisions J+{day_offset}')
        lines.append(f"📅 {titre_fc} :")
        if fc.get('vent'):
            lines.append(f"🌬️ {fc['vent']}")
        if fc.get('mer'):
            lines.append(f"🌊 {fc['mer']}")
        if fc.get('houle') and fc.get('houle') != "HOULE : confondue avec la mer du vent.":
            lines.append(f"🌊 {fc['houle']}")
        if fc.get('temps'):
            lines.append(f"⛅ {fc['temps']}")
        if fc.get('visibilite'):
            lines.append(f"👁️ {fc['visibilite']}")

    # Tendance ultérieure
    tendance_key = "j2" if day_offset <= 1 else "j3"
    if tendance_key in bmr_data:
        tend = bmr_data[tendance_key]
        lines.append(f"\n📉 {tend.get('titre', 'Tendance ultérieure')} :")
        if tend.get('vent'):
            lines.append(f"• {tend['vent']}")
        if tend.get('mer'):
            lines.append(f"• {tend['mer']}")

    return "\n".join(lines).strip()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extraction directe Météo Marine Météo-France (Token 0)")
    parser.add_argument("--client", default="RADIO 6", help="Nom du client radio (ex: RADIO 6, NORD, MONA)")
    parser.add_argument("--domain", default=None, help="Domaine BMS (ex: BMSCOTE-01-01)")
    parser.add_argument("--day-offset", type=int, default=1, help="Décalage jour (0=aujourd'hui, 1=demain)")
    args = parser.parse_args()

    client = args.domain if args.domain else args.client
    txt = get_marine_text_for_client(client, day_offset=args.day_offset)
    print(txt)
