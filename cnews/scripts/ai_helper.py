
def deg_to_compass(deg):
    if deg is None:
        return "secteur ouest"
    try:
        val = int((float(deg) / 45) + 0.5) % 8
        sectors = ["Nord", "Nord-Est", "Est", "Sud-Est", "Sud", "Sud-Ouest", "Ouest", "Nord-Ouest"]
        return f"secteur {sectors[val]}"
    except Exception:
        return "secteur ouest"
import os
import sys
import json
import datetime
import urllib.request
import urllib.error
import re
from dotenv import load_dotenv

# Load env from project root
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env.local"))

import unicodedata

FRENCH_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FRENCH_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                 "septembre", "octobre", "novembre", "décembre"]

def normalize_city(name):
    s = str(name).strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-6.1-terra")
OPENROUTER_VISION_MODEL = os.environ.get("OPENROUTER_VISION_MODEL", "google/gemini-2.5-flash")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def french_date(date_obj, include_year=True):
    wd = FRENCH_WEEKDAYS[date_obj.weekday()]
    mo = FRENCH_MONTHS[date_obj.month - 1]
    if include_year:
        return f"{wd} {date_obj.day} {mo} {date_obj.year}"
    return f"{wd} {date_obj.day} {mo}"


def get_api_key():
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def read_meteofrance_local_data():
    """Reads local Météo-France CSV files from Cartes Alerte to prefer exact Météo-France temperatures."""
    mf_data = {}
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    csv_dir = os.path.abspath(os.path.join(project_root, "..", "cartes_alertes"))
    if not os.path.exists(csv_dir):
        csv_dir = os.path.expanduser(r"~\Desktop\cartes_alertes")
        
    if os.path.exists(csv_dir):
        for fname in os.listdir(csv_dir):
            if fname.startswith("meteofrance_daily_forecast") and fname.endswith(".csv"):
                try:
                    import csv
                    with open(os.path.join(csv_dir, fname), "r", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f, delimiter=";")
                        for row in reader:
                            cname = normalize_city(row.get("Ville", ""))
                            try:
                                lat_val = float(row.get("Latitude", 0))
                                lon_val = float(row.get("Longitude", 0))
                            except ValueError:
                                lat_val, lon_val = 0, 0
                            coord_key = f"{round(lat_val, 2)}_{round(lon_val, 2)}"
                            
                            date_str = row.get("Date", "").strip()
                            if not date_str:
                                continue
                            try:
                                import math
                                tmin = int(math.floor(float(row.get("temperature_2m_min", 0)) + 0.5))
                                tmax = int(math.floor(float(row.get("temperature_2m_max", 0)) + 0.5))
                                code = int(row.get("weathercode", 0))
                                label = row.get("Temps_Label", "").strip().upper()
                            except (ValueError, TypeError):
                                continue
                                
                            if coord_key not in mf_data:
                                mf_data[coord_key] = {}
                            mf_data[coord_key][date_str] = {"tmin": tmin, "tmax": tmax, "code": code, "label": label, "wind": 15, "rain": 0.0}
                            
                            if cname:
                                if cname not in mf_data:
                                    mf_data[cname] = {}
                                mf_data[cname][date_str] = {"tmin": tmin, "tmax": tmax, "code": code, "label": label, "wind": 15, "rain": 0.0}
                except Exception:
                    pass
    return mf_data


def fetch_weather_struct(cities, day_offset):
    """Fetch real weather struct, prioritizing official Météo-France CSV data with fallback to Open-Meteo."""
    if not cities:
        return []
    selected = [c for c in cities if isinstance(c, dict) and "lat" in c and "lon" in c][:10]
    if not selected:
        return []
    mf_data = read_meteofrance_local_data()
    
    lats = ",".join(str(c["lat"]) for c in selected)
    lons = ",".join(str(c["lon"]) for c in selected)
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        f"&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_gusts_10m_max,weather_code,precipitation_sum"
        f"&timezone=Europe/Paris&forecast_days=10"
    )
    results_list = []
    import time
    data = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as e:
            if attempt == 2:
                print(f"Warning: Could not fetch weather struct: {e}")
                return []
            print(f"  [open-meteo struct] Retry {attempt+1}/3 due to: {e}")
            time.sleep(3)
            
    if data:
        results = data if isinstance(data, list) else [data]
        for i, r in enumerate(results):
                city = selected[i]
                cname = normalize_city(city["name"])
                try:
                    lat_val = float(city["lat"])
                    lon_val = float(city["lon"])
                except ValueError:
                    lat_val, lon_val = 0, 0
                coord_key = f"{round(lat_val, 2)}_{round(lon_val, 2)}"
                
                d = r.get("daily", {})
                if not d:
                    continue
                try:
                    target_date = datetime.date.today() + datetime.timedelta(days=day_offset)
                    target_date_str = target_date.strftime("%d/%m/%Y")
                    
                    # Prefer exact Météo-France CSV data if available for this coordinate or city name
                    if coord_key in mf_data and target_date_str in mf_data[coord_key]:
                        mf_item = mf_data[coord_key][target_date_str]
                        tmin = mf_item["tmin"]
                        tmax = mf_item["tmax"]
                        code = mf_item["code"]
                        label = mf_item.get("label", "")
                    elif cname in mf_data and target_date_str in mf_data[cname]:
                        mf_item = mf_data[cname][target_date_str]
                        tmin = mf_item["tmin"]
                        tmax = mf_item["tmax"]
                        code = mf_item["code"]
                        label = mf_item.get("label", "")
                    else:
                        closest_mf = None
                        min_dist = 0.15 # ~15km radius tolerance
                        for mf_key, dates in mf_data.items():
                            if "_" in mf_key:
                                try:
                                    mlat, mlon = map(float, mf_key.split("_"))
                                    dist = ((lat_val - mlat)**2 + (lon_val - mlon)**2)**0.5
                                    if dist < min_dist and target_date_str in dates:
                                        min_dist = dist
                                        closest_mf = mf_key
                                except ValueError:
                                    pass
                        if closest_mf:
                            mf_item = mf_data[closest_mf][target_date_str]
                            tmin = mf_item["tmin"]
                            tmax = mf_item["tmax"]
                            code = mf_item["code"]
                            label = mf_item.get("label", "")
                        else:
                            import math
                            tmin = int(math.floor(d.get("temperature_2m_min", [0]*8)[day_offset] + 0.5))
                            tmax = int(math.floor(d.get("temperature_2m_max", [0]*8)[day_offset] + 0.5))
                            code = int(d.get("weathercode", [0]*8)[day_offset])
                    wind_raw = float(d["wind_speed_10m_max"][day_offset])
                    wind = int(round(wind_raw / 5.0) * 5)
                    gust_list = d.get("wind_gusts_10m_max", [])
                    if gust_list and len(gust_list) > day_offset and gust_list[day_offset] is not None:
                        gust_raw = float(gust_list[day_offset])
                    else:
                        gust_raw = wind_raw * 1.3
                    gust = int(round(gust_raw / 5.0) * 5)
                    rain = round(d["precipitation_sum"][day_offset], 1)
                    label_val = label if 'label' in locals() and label else ""
                    results_list.append({
                        "city": city["name"],
                        "tmin": tmin,
                        "tmax": tmax,
                        "wind": wind,
                        "gust": gust,
                        "code": code,
                        "label": label_val,
                        "rain": rain
                    })
                except (IndexError, KeyError):
                    pass
    return results_list


def fetch_raw_weather(cities, day_offset):
    """Fetch real weather data formatted as string from Open-Meteo."""
    struct = fetch_weather_struct(cities, day_offset)
    lines = []
    for s in struct:
        gust_str = f", Rafales {s.get('gust', s['wind'])} km/h" if s.get('gust', 0) >= 40 else ""
        lbl_str = f"Temps officiel Météo-France : {s['label']}, " if s.get('label') else ""
        wind_dir = deg_to_compass(s.get('wind_deg', 230))
        lines.append(
            f"- {s['city']} : {lbl_str}Min {s['tmin']}°C, Max {s['tmax']}°C, "
            f"Vent {s['wind']} km/h ({wind_dir}){gust_str}"
        )
    return "\n".join(lines)


def extract_xml_tag(text, tag):
    if not text or not isinstance(text, str):
        return ""
    # Remove markdown code blocks first to make matching clean
    clean_text = re.sub(r'```[a-zA-Z]*\n', '', text)
    clean_text = clean_text.replace('```', '')
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", clean_text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def call_openrouter(api_key, prompt, images=None):
    """Call OpenRouter API with the given prompt and optional multimodal images. Returns the response text."""
    if images and any(images):
        valid_images = [img for img in images if img]
        model = OPENROUTER_VISION_MODEL
        content_list = [{"type": "text", "text": prompt}]
        for img_url in valid_images:
            content_list.append({"type": "image_url", "image_url": {"url": img_url}})
        messages = [{"role": "user", "content": content_list}]
        print(f"   [Mode Visio Actif 👁️] Envoi de {len(valid_images)} cartes HD Météo-France au modèle Vision : {model}")
    else:
        model = OPENROUTER_MODEL
        messages = [{"role": "user", "content": prompt}]

    payload_dict = {
        "model": model,
        "messages": messages,
        "max_tokens": 4000,
    }
    # Si modèle de raisonnement OpenAI (Terra / GPT / o1 / o3)
    if not (images and any(images)) and any(k in model.lower() for k in ["terra", "o1", "o3", "gpt-5", "gpt-6"]):
        effort = os.environ.get("REASONING_EFFORT", "medium")
        payload_dict["reasoning"] = {"effort": effort}
        print(f"   [Terra Rédaction 🧠] Raisonnement activé au niveau medium : effort = {effort}")
    else:
        payload_dict["temperature"] = 0.5

    payload = json.dumps(payload_dict).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cnews.weather.local",
        "X-Title": "CNews Bulletin Météo",
    }
    req = urllib.request.Request(OPENROUTER_API_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]


def generate_local_fallback_texts(client_name, cities, day_offset):
    """Générateur de secours local et déterministe basé 100% sur Open-Meteo et les dates exactes."""
    today = datetime.date.today()
    d1 = today + datetime.timedelta(days=day_offset)
    d2 = today + datetime.timedelta(days=day_offset + 1)
    d3 = today + datetime.timedelta(days=day_offset + 2)
    d4 = today + datetime.timedelta(days=day_offset + 3)
    d5 = today + datetime.timedelta(days=day_offset + 4)

    j1_struct = fetch_weather_struct(cities, day_offset)
    j2_struct = fetch_weather_struct(cities, day_offset + 1)
    j3_struct = fetch_weather_struct(cities, day_offset + 2)
    j4_struct = fetch_weather_struct(cities, day_offset + 3)
    j5_struct = fetch_weather_struct(cities, day_offset + 4)

    wd1 = FRENCH_WEEKDAYS[d1.weekday()]
    wd2 = FRENCH_WEEKDAYS[d2.weekday()]

    date_j1 = french_date(d1, include_year=True)
    date_j2 = french_date(d2, include_year=False)
    date_j3 = french_date(d3, include_year=False)
    date_j4 = french_date(d4, include_year=False)
    date_j5 = french_date(d5, include_year=False)

    import random
    def format_min_list(struct):
        if not struct:
            return "des températures matinales douces"
        sampled = random.sample(struct, min(5, len(struct)))
        return ", ".join(f"{s['tmin']}°C à {s['city']}" for s in sampled)

    def format_max_list(struct):
        if not struct:
            return "des températures maximales estivales"
        sampled = random.sample(struct, min(5, len(struct)))
        return ", ".join(f"{s['tmax']}°C à {s['city']}" for s in sampled)

    # Déterminer la situation synoptique générale depuis les codes météo J1
    avg_max_j1 = sum(s['tmax'] for s in j1_struct) / len(j1_struct) if j1_struct else 25
    has_rain_j1 = any(s['rain'] > 1.0 or s['code'] >= 50 for s in j1_struct)
    has_storm_j1 = any(s['code'] >= 90 for s in j1_struct)

    if has_storm_j1:
        synop = "déstabilisation d'une masse d'air sous l'effet d'une hausse de l'instabilité diurne"
        ambiance_matin = "déjà lourde au lever du jour, avec des cumulus bourgeonnant rapidement"
        ambiance_aprem = "un ciel chaotique devenant menaçant avec le déclenchement d'averses orageuses localement fortes"
    elif avg_max_j1 >= 30:
        synop = "poussée d'une dorsale anticyclonique d'altitude advectant une masse d'air très chaude"
        ambiance_matin = "très douce au lever du jour sous un soleil prédominant"
        ambiance_aprem = "un ensoleillement généreux sous une chaleur estivale bien marquée"
    elif avg_max_j1 >= 24:
        synop = "installation d'un marais barométrique estival assurant un temps sec et lumineux"
        ambiance_matin = "douce et agréable au lever du jour"
        ambiance_aprem = "un soleil généreux avec des températures de saison sans excès de chaleur"
    elif has_rain_j1:
        synop = "circulation d'un flux océanique d'ouest perturbé et humide"
        ambiance_matin = "grise et humide, caractérisée par des bancs de stratus bas et des passages de pluies"
        ambiance_aprem = "un ciel dominé par des passages nuageux denses accompagnés d'averses éparses"
    else:
        synop = "influence anticyclonique calme assurant un temps sec et modérément doux"
        ambiance_matin = "lumineuse et vivifiante après la dissipation des rares brumes matinales"
        ambiance_aprem = "un ensoleillement agréable avec des températures de saison particulièrement douces"

    if avg_max_j1 >= 28:
        evolution_temp = "un réchauffement diurne nettement marqué"
    elif avg_max_j1 >= 23:
        evolution_temp = "une hausse progressive des températures jusqu'à des valeurs agréables"
    else:
        evolution_temp = "des températures très douces et de saison, sans aucun excès"

    def get_clean_region_name(cname):
        c = cname.strip().upper()
        if "ROCHELLE" in c or "NAQ" in c:
            return "en Charente-Maritime et sur le littoral"
        if "NORD" in c or "6" in c or "MONA" in c:
            return "sur les Hauts-de-France"
        if "NORMANDIE" in c or "SEINE" in c:
            return "sur la Normandie"
        if "BRETAGNE" in c or "BREIZH" in c:
            return "sur la Bretagne"
        if "PACA" in c or "AZUR" in c or "PROVENCE" in c:
            return "sur la Côte d'Azur et la Provence"
        if "AUVERGNE" in c or "RHONE" in c:
            return "sur l'Auvergne-Rhône-Alpes"
        if "BOURGOGNE" in c or "FRANCHE" in c:
            return "sur la Bourgogne-Franche-Comté"
        if "GRAND EST" in c or "ALSACE" in c:
            return "sur le Grand Est"
        if "ILE-DE-FRANCE" in c or "PARIS" in c:
            return "sur l'Île-de-France"
        if "OCCITANIE" in c:
            return "sur l'Occitanie"
        if "CORSE" in c:
            return "sur la Corse"
        if "PAYS DE LA LOIRE" in c:
            return "sur les Pays de la Loire"
        if "CENTRE" in c:
            return "sur le Centre-Val de Loire"
        return "sur la région"

    region_label = get_clean_region_name(client_name)
    min_val_str = f"{int(round(min(s['tmin'] for s in j1_struct)))}°C" if j1_struct else "18°C"
    max_val_str = f"{int(round(max(s['tmax'] for s in j1_struct)))}°C" if j1_struct else "28°C"

    if has_storm_j1:
        essential_sky = "Un temps lourd et instable domine avec un risque d'orages localement forts"
        summary_lancement = f"⚡ TEMPS LOURD ET INSTABLE : Des averses orageuses parfois fortes sont attendues ce {wd1} sur la région, avec des températures évoluant de {min_val_str} le matin jusqu'à {max_val_str} l'après-midi."
        last_sentence_morning = "L'atmosphère devient rapidement instable avec des nuages menaçants et des averses orageuses."
    elif has_rain_j1:
        essential_sky = "Un passage pluvieux et nuageux traverse le secteur au fil de la journée"
        summary_lancement = f"🌧️ PASSAGE PLUVIEUX ET NUAGEUX : Des précipitations et des averses traversent la région ce {wd1}, avec des températures évoluant de {min_val_str} le matin jusqu'à {max_val_str} l'après-midi."
        last_sentence_morning = "Des passages nuageux denses et des averses régulières arrosent le secteur au fil de la matinée."
    elif avg_max_j1 >= 30:
        essential_sky = "Un grand soleil et une forte chaleur estivale s'imposent"
        summary_lancement = f"☀️ SOLEIL ET CHALEUR ESTIVALE : Un ciel très ensoleillé s'impose ce {wd1} sur la région, avec des maximales grimpant jusqu'à {max_val_str}."
        last_sentence_morning = "La matinée se poursuit sous un ensoleillement en progression constante."
    else:
        essential_sky = "Un temps calme, lumineux et ensoleillé s'impose"
        summary_lancement = f"🌤️ TEMPS CALME ET LUMINEUX : Un ciel agréablement dégagé s'impose ce {wd1} sur la région, avec des températures évoluant de {min_val_str} le matin jusqu'à {max_val_str} l'après-midi."
        last_sentence_morning = "La matinée se poursuit sous un ensoleillement en progression constante."

    today_summary = (
        f"🌤️ L'ESSENTIEL DE CE {wd1.upper()} : {essential_sky} {region_label}. "
        f"Au lever du jour, les températures minimales affichent {min_val_str}, "
        f"avant de grimper jusqu'à {max_val_str} sous abri au cours de l'après-midi. "
        f"Le vent souffle de manière modérée, assurant une agréable ventilation."
    )

    summary_morning = (
        f"Ce {wd1} matin, le réveil se fait dans une ambiance {ambiance_matin}. "
        f"Les températures au lever du jour s'échelonnent avec {format_min_list(j1_struct)}. "
        f"Le vent souffle discrètement, maintenant un ressenti très doux sur l'ensemble du secteur. "
        f"{last_sentence_morning}"
    )

    summary_afternoon = (
        f"L'après-midi, le temps est marqué par {ambiance_aprem}. "
        f"Le thermomètre affiche des maximales atteignant {format_max_list(j1_struct)}. "
        f"Les zones littorales profitent d'une brise marine rafraîchissante qui modère les ardeurs du mercure. "
        f"En soirée, l'atmosphère conserve une grande douceur idéale pour les sorties et activités de plein air."
    )

    summary_morning2 = (
        f"Ce {wd2} matin ({date_j2}), la journée débute sous un ciel généralement lumineux et peu nuageux. "
        f"Les températures matinales sont douces et affichent {format_min_list(j2_struct)}. "
        f"Les vents matinaux restent faibles à modérés, assurant une excellente visibilité. "
        f"La matinée s'annonce agréable et propice aux activités extérieures."
    )

    summary_afternoon2 = (
        f"L'après-midi du {date_j2}, le soleil s'impose malgré quelques passages nuageux inoffensifs. "
        f"Les températures maximales évoluent vers {format_max_list(j2_struct)}. "
        f"L'ambiance reste estivale et lumineuse avec un vent faible à modéré."
    )

    def get_trend_line(date_str, struct):
        if not struct:
            return f"▶ {date_str} : Temps ensoleillé et agréable. Minimales vers 15°C. Maximales atteignant 25°C. Vent modéré."
        tmin = min(s['tmin'] for s in struct) if struct else 15
        tmax = max(s['tmax'] for s in struct) if struct else 25
        code = max((s['code'] for s in struct), default=0)
        max_gust = max((s.get('gust', 0) for s in struct), default=0)
        gust_info = f" Attention aux rafales de vent atteignant {max_gust} km/h." if max_gust >= 40 else " Le vent souffle de manière modérée."
        if code >= 80:
            desc = "Temps instable avec des passages d'averses orageuses."
        elif code >= 50:
            desc = "Ciel nuageux à couvert avec des pluies éparses."
        elif tmax >= 32:
            desc = "Poursuite d'une forte chaleur sous un soleil généreux."
        else:
            desc = "Temps calme, sec et largement lumineux."
        return (
            f"▶ {date_str} : {desc} "
            f"Au lever du jour, les températures minimales oscillent autour de {tmin}°C. "
            f"L'après-midi, le mercure grimpe jusqu'à {tmax}°C sous abri.{gust_info}"
        )

    forecast_raw = (
        f"📉 Tendance – 3 jours suivants ({date_j3} au {date_j5})\n\n"
        f"{get_trend_line(date_j3, j3_struct)}\n\n"
        f"{get_trend_line(date_j4, j4_struct)}\n\n"
        f"{get_trend_line(date_j5, j5_struct)}"
    )

    vig_warning = f"⚠️ Forte Chaleur : Épisode de chaleur marqué sur la région ce {wd1}.\n" if avg_max_j1 >= 32 else ""
    records_raw = (
        f"{vig_warning}"
        f"🌧️ Pluies & Humidité : Temps sec sur la région ce {wd1} avec maintien de la sécheresse de surface.\n"
        f"🌬️ Vent & Rafales : Vent faible à modéré (15 à 25 km/h) ce {wd1}. Aucun coup de vent à craindre.\n"
        f"⚡ Risque Orageux : Ambiance calme et stable sur l'ensemble du secteur ce {wd1}.\n"
        f"🌫️ Brouillards & Visibilité : Quelques rares grisailles matinales au lever du jour, se dissipant rapidement."
    )

    # Mountain text : uniquement pour le client national (Europe 1), calibré selon la saison réelle
    is_national = "EUROPE" in client_name.upper() or "NATIONAL" in client_name.upper()
    if not is_national:
        mountain_text = ""
        mountain_title = ""
    else:
        mountain_title = f"🏔️ MÉTÉO DES MONTAGNES DU {date_j1.upper()}"
        month = d1.month
        if month in [11, 12, 1, 2, 3]:  # Hiver
            alpes_desc = f"Conditions froides et sèches ce {wd1}. Gelées matinales généralisées en altitude, températures maximales fraîches en vallée. Isotherme 0°C oscillant vers 2200m."
            pyrenees_desc = f"Ciel bien dégagé ce {wd1}. Fraîcheur vive sur les sommets avec un vent de crête modéré. Inversion thermique possible en matinée."
            mc_desc = f"Temps lumineux mais piquant ce {wd1}. Brumes matinales dans les creux, journée fraîche et dégagée sur les crêtes."
            vosges_desc = f"Atmosphère vivifiante et ensoleillée ce {wd1}. Gelées blanches au lever du jour, après-midi sèche et calme."
            jura_desc = f"Temps sec et froid ce {wd1}. Beau soleil sur les plateaux après dissipation des grisailles locales."
            corse_desc = f"Beau temps dominant sur le relief corse ce {wd1}, vent d'altitude modéré sur les cols."
        elif month in [9, 10, 4]:  # Automne / Printemps
            alpes_desc = f"Ambiance fraîche en altitude ce {wd1} avec de belles éclaircies après dissipation des bancs de brume matinaux en vallée. Isotherme 0°C autour de 2800 m, vent faible sur les crêtes."
            pyrenees_desc = f"Soleil prédominant en matinée ce {wd1}, voilé par quelques nuages élevés sans intempérie. Températures douces en piémont et fraîches en haute altitude."
            mc_desc = f"Matinée fraîche avec quelques inversions thermiques ce {wd1}, suivie d'un après-midi lumineux et doux sur les plateaux."
            vosges_desc = f"Temps calme et ensoleillé ce {wd1}. Fraîcheur matinale marquée, douceur agréable l'après-midi."
            jura_desc = f"Belles éclaircies automnales ce {wd1} sur les crêtes et combes après quelques brouillards matinaux."
            corse_desc = f"Temps très agréable et lumineux ce {wd1} sur les massifs insulaires, vent faible à modéré sur les crêtes."
        else:  # Été
            alpes_desc = f"Belle luminosité ce {wd1}, ambiance tempérée en altitude avec un faible risque d'ondées isolées en fin de journée sur les reliefs."
            pyrenees_desc = f"Soleil dominant ce {wd1}, cumulus bourgeonnants l'après-midi sur les crêtes frontière."
            mc_desc = f"Temps agréable et ensoleillé ce {wd1}, températures douces sur les plateaux."
            vosges_desc = f"Beau temps estival ce {wd1}, ciel dégagé et vent faible."
            jura_desc = f"Journée lumineuse et calme ce {wd1} sur l'ensemble du massif."
            corse_desc = f"Grand soleil ce {wd1} sur les crêtes comme sur les côtes."

        mountain_text = (
            f"🏔️ Météo montagne\n\n"
            f"    Alpes : {alpes_desc}\n\n"
            f"    Pyrénées : {pyrenees_desc}\n\n"
            f"    Massif central : {mc_desc}\n\n"
            f"    Vosges : {vosges_desc}\n\n"
            f"    Jura : {jura_desc}\n\n"
            f"    Corse : {corse_desc}"
        )

    # Météo Plages et Marine dynamiques calculées à partir des données réelles du jour
    max_wind_j1 = max((s.get('wind', 15) for s in j1_struct), default=15)
    max_gust_j1 = max((s.get('gust', 20) for s in j1_struct), default=20)
    coastal_max_t = max((s['tmax'] for s in j1_struct), default=24)
    coastal_min_t = min((s['tmin'] for s in j1_struct), default=15)
    
    if max_wind_j1 >= 25:
        sea_state_desc = "Mer peu agitée à agitée au large (vagues 0.8m à 1.3m), prudence pour la baignade"
    elif max_wind_j1 >= 15:
        sea_state_desc = "Mer peu agitée (vagues 0.4m à 0.8m), idéale pour les activités nautiques"
    else:
        sea_state_desc = "Mer belle et calme (vagues < 0.4m), excellente pour la baignade"

    gust_str = f" avec rafales atteignant {max_gust_j1} km/h" if max_gust_j1 >= 35 else ""

    if "NORD" in client_name.upper() or "HDF" in client_name.upper() or "RADIO 6" in client_name.upper() or "MONA" in client_name.upper():
        water_t_val = min(19, max(16, round(16.5 + (coastal_max_t - 20) * 0.15)))
        beach_text = (
            f"🏖️ MÉTÉO DES PLAGES – CÔTE D’OPALE & CÔTE PICARDE ({date_j1})\n\n"
            f"🌴 Dunkerque / Malo-les-Bains / Bray-Dunes\n"
            f"☀️ {synop.capitalize()} sur la côte. Températures de {coastal_min_t + 2}°C le matin à {coastal_max_t}°C l'après-midi.\n"
            f"🌊 Température de l’eau : {water_t_val}°C à {water_t_val + 1}°C\n\n"
            f"🌴 Le Touquet / Berck / Baie de Somme\n"
            f"☀️ Ambiance agréable sur le littoral ce {wd1}. Maximales atteignant {coastal_max_t + 1}°C sur le sable.\n"
            f"🌊 Température de l’eau : {water_t_val + 1}°C"
        )
        marine_text = (
            f"🌊 MÉTÉO MARINE – CÔTE D’OPALE & MER DU NORD ({date_j1})\n\n"
            f"📍 Zones : Dunkerque • Calais • Boulogne-sur-Mer • Le Touquet\n\n"
            f"☀️ Situation générale : {synop.capitalize()}.\n"
            f"🌬️ Vent : Vent de Nord-Est modéré ({max_wind_j1} km/h{gust_str}) ce {wd1}.\n"
            f"🌊 État de la mer : {sea_state_desc}.\n"
            f"⚠️ Visibilité : Bonne sur l'ensemble du littoral."
        )
    elif "ROCHELLE" in client_name.upper() or "NAQ" in client_name.upper():
        water_t_val = min(24, max(20, round(20.5 + (coastal_max_t - 22) * 0.2)))
        beach_text = (
            f"🏖️ MÉTÉO DES PLAGES – LITTORAL CHARENTAIS ({date_j1})\n\n"
            f"🌴 La Rochelle / Île de Ré / Île d’Oléron\n"
            f"☀️ {synop.capitalize()} sur l'ensemble des plages charentaises ce {wd1}.\n"
            f"🌡️ Température sur le littoral : {coastal_min_t + 3}°C à {coastal_max_t}°C\n"
            f"🌊 Température de l’eau : {water_t_val}°C à {water_t_val + 1}°C\n\n"
            f"🌴 Royan / Côte de Beauté / Rochefort\n"
            f"☀️ Ensoleillement généreux ce {wd1}, maximales de {coastal_max_t + 1}°C tempérées par la brise marine.\n"
            f"🌊 Température de l’eau : {water_t_val + 1}°C"
        )
        marine_text = (
            f"🌊 MÉTÉO LITTORALE & MARINE – CHARENTE-MARITIME ({date_j1})\n\n"
            f"📍 Zones : La Rochelle • Rochefort • Royan • Pertuis Breton & d'Antioche\n\n"
            f"☀️ Situation synoptique : {synop.capitalize()}.\n"
            f"🌬️ Vent : Régime de brises thermiques ({max_wind_j1} km/h{gust_str}).\n"
            f"🌊 État de la mer : {sea_state_desc}."
        )
    elif "NORMANDIE" in client_name.upper() or "NOR" in client_name.upper():
        water_t_val = min(20, max(17, round(17.0 + (coastal_max_t - 20) * 0.18)))
        beach_text = (
            f"🏖️ MÉTÉO DES PLAGES – CÔTE D’ALBÂTRE & MANCHE ({date_j1})\n\n"
            f"🌴 Le Havre / Deauville / Cabourg\n"
            f"☀️ Temps agréable ce {wd1}. Températures sur les plages : {coastal_min_t + 2}°C à {coastal_max_t}°C.\n"
            f"🌊 Température de l’eau : {water_t_val}°C à {water_t_val + 1}°C\n\n"
            f"🌴 Dieppe / Fécamp / Cherbourg\n"
            f"☀️ Belle luminosité ce {wd1}, brise littorale rafraîchissante avec maximales de {coastal_max_t}°C.\n"
            f"🌊 Température de l’eau : {water_t_val}°C"
        )
        marine_text = (
            f"🌊 MÉTÉO MARINE – MANCHE & LITTORAL NORMAND ({date_j1})\n\n"
            f"📍 Zones : Baie de Seine • Le Havre • Fécamp • Dieppe • Cherbourg\n\n"
            f"☀️ Situation générale : {synop.capitalize()}.\n"
            f"🌬️ Vent : Vent modéré de Nord-Est ({max_wind_j1} km/h{gust_str}) ce {wd1}.\n"
            f"🌊 État de la mer : {sea_state_desc}."
        )
    else:
        beach_text = ""
        marine_text = ""

    summary_lancement2 = f"🌤️ PREVISIONS DU {wd2.upper()} : Poursuite d'un temps calme et ensoleillé sur la région avec des températures de saison."
    forecast_lancement = f"📉 EVOLUTION DE LA TENDANCE : Un temps sec et lumineux prédomine pour la suite de la semaine avec une douceur estivale durable."

    return {
        "todaySummary": today_summary,
        "summaryLancement": summary_lancement,
        "summaryLancement2": summary_lancement2,
        "summaryMorning": summary_morning,
        "summaryAfternoon": summary_afternoon,
        "summaryMorning2": summary_morning2,
        "summaryAfternoon2": summary_afternoon2,
        "forecastRaw": forecast_raw,
        "forecastTextRaw": forecast_raw,
        "forecastLancement": forecast_lancement,
        "recordsRaw": records_raw,
        "mountain": mountain_text,
        "mountainTitle": f"🏔️ MÉTÉO DES MONTAGNES DU {date_j1.upper()}",
        "beach": beach_text,
        "marine": marine_text
    }


def fetch_vigilance_and_national_context(day_offset=1):
    """Runs vigilance skill summary, prioritizing J+2 to J+7 trends and vigilance alerts, skipping Section 2 if day_offset >= 1."""
    try:
        import subprocess
        import re
        script_path = os.path.expanduser(r"~\.gemini\config\skills\vigilance\scripts\get_vigilance_data.py")
        if os.path.exists(script_path):
            res = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout:
                out = res.stdout.strip()
                parts = out.split("==================================================")
                if len(parts) >= 3:
                    p1_trend = parts[0].strip()[:1400] # Résumé PDF J+2 à J+7
                    p2_bulletin = parts[1].strip()[:1200] # Bulletin national meteofrance.com
                    
                    p3_raw_vigilance = parts[2].strip()
                    # Découpage par jour pour ne garder que le jour ciblé et éviter la troncature
                    vig_blocks = re.split(r'###\s*Vigilance\s+météo\s+et\s+crues\s+pour', p3_raw_vigilance, flags=re.IGNORECASE)
                    
                    target_block = ""
                    if len(vig_blocks) > 1:
                        target_idx = min(day_offset, 1) + 1
                        if target_idx < len(vig_blocks):
                            target_block = "### Vigilance météo et crues pour" + vig_blocks[target_idx]
                        else:
                            target_block = "### Vigilance météo et crues pour" + vig_blocks[-1]
                    else:
                        target_block = p3_raw_vigilance[:1800]
                    
                    p3_vigilance = target_block.strip()

                    if day_offset >= 1:
                        # Si on vise le prochain jour (J+1 / Demain ou plus), on ignore le bulletin meteofrance.com (souvent en retard sur J+0)
                        # et on met en avant le PDF J+2/J+7 + la liste complète de la Vigilance Orange/Jaune
                        return f"=== TENDANCE PROCHAINS JOURS (J+2 à J+7) ===\n{p1_trend}\n\n=== VIGILANCE DÉPARTEMENTALE OFFICIELLE ===\n{p3_vigilance}"
                    else:
                        return f"=== BULLETIN NATIONAL DU JOUR ===\n{p2_bulletin}\n\n=== VIGILANCE DÉPARTEMENTALE OFFICIELLE ===\n{p3_vigilance}\n\n=== TENDANCE PROCHAINS JOURS (J+2 à J+7) ===\n{p1_trend[:800]}"
                return out[:2800]
    except Exception:
        pass
    return "Vigilance verte ou jaune sur la majorité du territoire national. Chaleur de saison sur la moitié nord et plus marquée sur les régions du sud."


def get_client_region_prefix(client_name):
    """Determine region prefix for map image lookup based on client name."""
    c_clean = client_name.strip().upper()
    if "EUROPE" in c_clean:
        return ""
    elif any(k in c_clean for k in ["NORD", "RADIO 6", "MONA"]):
        return "hdf_"
    elif "ROCHELLE" in c_clean:
        return "naq_"
    elif "NORMANDIE" in c_clean:
        return "normandie_"
    return ""


def generate_bulletin_texts(client_name, cities, day_offset, images=None):
    """Generate rich AI forecast texts using OpenRouter / Vision multimodal models, with bulletproof deterministic fallback."""
    local_fallback = generate_local_fallback_texts(client_name, cities, day_offset)

    api_key = get_api_key()
    if not api_key:
        print("Notice: No OPENROUTER_API_KEY found — using bulletproof local deterministic fallback.")
        return local_fallback

    # Automatic Vision Mode image discovery if not passed directly
    vision_images = images
    if not vision_images and os.environ.get("VISION_MODE", "true").lower() in ["true", "1", "yes"]:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        maps_dir = os.path.abspath(os.path.join(project_root, "..", "cartes_alertes"))
        if not os.path.exists(maps_dir):
            maps_dir = os.path.expanduser(r"~\Desktop\cartes_alertes")
            
        reg_prefix = get_client_region_prefix(client_name)
        img_m_path = os.path.join(maps_dir, f"carte_{reg_prefix}J{day_offset}_matin.jpg")
        img_a_path = os.path.join(maps_dir, f"carte_{reg_prefix}J{day_offset}_apresmidi.jpg")
        img_m2_path = os.path.join(maps_dir, f"carte_{reg_prefix}J{day_offset+1}_matin.jpg")
        img_a2_path = os.path.join(maps_dir, f"carte_{reg_prefix}J{day_offset+1}_apresmidi.jpg")
        discovered_imgs = [None, None, None, None]
        print(f"  [Vision Mode] Découverte des cartes dans : {maps_dir}")
        paths = [img_m_path, img_a_path, img_m2_path, img_a2_path]
        for i, p in enumerate(paths):
            if os.path.exists(p):
                print(f"    -> Carte trouvée et chargée : {os.path.basename(p)}")
                try:
                    import base64
                    with open(p, "rb") as f:
                        b64_str = base64.b64encode(f.read()).decode("utf-8")
                        discovered_imgs[i] = f"data:image/jpeg;base64,{b64_str}"
                except Exception as e:
                    print(f"Warning: Could not read map image {p} for Vision Mode: {e}")
            else:
                print(f"    -> Carte manquante (passée) : {os.path.basename(p)}")
        if any(discovered_imgs):
            vision_images = discovered_imgs

    # Chargement de la planche officielle de référence des 13 pictogrammes pour injection dans le modèle vision
    ref_board_b64 = None
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidate_ref_paths = [
        os.path.join(project_root, "meteo_cnews_2", "pictogramme", "planche_reference_pictos.jpg"),
        os.path.join(project_root, "..", "meteo_cnews_2", "pictogramme", "planche_reference_pictos.jpg"),
        os.path.expanduser(r"~\Documents\METEO_CLIMAT\meteo-kappa\meteo_cnews_2\pictogramme\planche_reference_pictos.jpg")
    ]
    for p_ref in candidate_ref_paths:
        if os.path.exists(p_ref):
            try:
                import base64
                with open(p_ref, "rb") as f_ref:
                    ref_board_b64 = f"data:image/jpeg;base64,{base64.b64encode(f_ref.read()).decode('utf-8')}"
                    print(f"  [Vision Mode 👁️] Planche de référence visuelle chargée : {os.path.basename(p_ref)}")
                    break
            except Exception as e_ref:
                print(f"Warning: Could not read picto reference board: {e_ref}")

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        try:
            from backports.zoneinfo import ZoneInfo
        except ImportError:
            ZoneInfo = None

    if ZoneInfo:
        PARIS_TZ = ZoneInfo("Europe/Paris")
        today = datetime.datetime.now(PARIS_TZ).date()
    else:
        today = datetime.date.today()

    d1 = today + datetime.timedelta(days=day_offset)
    d2 = today + datetime.timedelta(days=day_offset + 1)
    d3 = today + datetime.timedelta(days=day_offset + 2)
    d4 = today + datetime.timedelta(days=day_offset + 3)
    d5 = today + datetime.timedelta(days=day_offset + 4)
    d6 = today + datetime.timedelta(days=day_offset + 5)
    d7 = today + datetime.timedelta(days=day_offset + 6)

    date_j1 = french_date(d1, include_year=True)
    date_j2 = french_date(d2, include_year=False)
    date_j3 = french_date(d3, include_year=False)
    date_j4 = french_date(d4, include_year=False)
    date_j5 = french_date(d5, include_year=False)
    date_j6 = french_date(d6, include_year=False)
    date_j7 = french_date(d7, include_year=False)

    print(f"Fetching weather data for AI generation ({client_name}, offset={day_offset})...")
    j1_data = fetch_raw_weather(cities, day_offset)
    j2_data = fetch_raw_weather(cities, day_offset + 1)
    j3_data = fetch_raw_weather(cities, day_offset + 2)
    j4_data = fetch_raw_weather(cities, day_offset + 3)
    j5_data = fetch_raw_weather(cities, day_offset + 4)
    vig_context = fetch_vigilance_and_national_context(day_offset)

    local_fallback = generate_local_fallback_texts(client_name, cities, day_offset)

    picto_guide = """RÉFÉRENTIEL OFFICIEL DES 13 PICTOGRAMMES (BANQUE OFFICIELLE METEO-KAPPA) :
  1. "P1 (soleil).png" : Grand soleil éclatant jaune d'or sur fond bleu, sans aucun nuage. -> Temps ensoleillé franc, ciel bleu.
  2. "P2 (peu nuageux).png" : Soleil dominant avec un tout petit cumulus blanc discret. -> Ciel largement ensoleillé, rares cumulus.
  3. "P8 (nuageux).png" : Soleil masqué de moitié par un nuage blanc avec ombres. -> Belles éclaircies et passages nuageux (ciel partagé).
  4. "P4 (très nuageux).png" : Dominante de nuages grisâtres denses masquant le soleil avec juste un petit éclat. -> Ciel très nuageux, courtes éclaircies.
  5. "P5 (couvert).png" (⚠️ GRISAILLE) : Double couche de nuages GRIS SOMBRE/FONCÉ sans aucun soleil ni rayon (0% jaune). -> Ciel totalement couvert, bouché, grisaille uniforme.
  6. "P6 (soleil voilé).png" (⚠️ CIEL VOILÉ) : Voile nuageux BLANC LAITEUX lumineux avec le SOLEIL BIEN VISIBLE EN TRANSPARENCE ou sous le voile blanc. -> Ciel voilé mais lumineux, soleil tamisé par des nuages d'altitude (CE N'EST PAS un ciel couvert !).
  7. "P9 (averses).png" : Nuage avec des gouttes de pluie bleu foncé et un soleil jaune bien visible derrière. -> Averses entrecoupées d'éclaircies.
  8. "P10 (pluies faibles).png" : Nuage gris/blanc avec de fines gouttes de pluie continues. -> Ciel gris, pluies faibles ou bruines.
  9. "P11 (fortes pluies).png" : Nuage gris sombre avec des lignes de pluie denses et soutenues. -> Pluies continues soutenues ou abondantes.
  10. "P12 (neige).png" : Nuage avec des cristaux/flocons de neige (étoiles blanches). -> Chutes de neige ou averses neigeuses.
  11. "orages.png" (⚠️ CRITIQUE) : Nuage sombre zébré d'un éclair / coup de foudre jaune/blanc très visible. -> Risque d'orages, foudre.
  12. "Orages accompagnés de grêle.png" (⚠️ CRITIQUE) : Nuage zébré d'un éclair avec des grêlons (billes blanches/noires). -> Orages violents avec risque de grêle.
  13. "brouillards.png" : Trois nappes horizontales grises/blanches superposées au ras du sol. -> Brumes denses ou brouillards réduisant la visibilité.

⚠️ DISTINCTION STRICTE CIEL COUVERT vs SOLEIL VOILÉ :
- Si la silhouette est un nuage GRIS FONCÉ/SOMBRE sans aucun soleil : c'est impérativement P5 (couvert) -> décris un ciel couvert, sombre, sans éclaircie.
- Si la silhouette est un voile BLANC LAITEUX avec le disque ou halo du soleil jaune présent : c'est impérativement P6 (soleil voilé) -> décris un ciel voilé mais lumineux, soleil tamisé, ensoleillement filtré."""

    # Split images list
    img_m1 = vision_images[0] if (vision_images and len(vision_images) > 0) else None
    img_a1 = vision_images[1] if (vision_images and len(vision_images) > 1) else None
    img_m2 = vision_images[2] if (vision_images and len(vision_images) > 2) else None
    img_a2 = vision_images[3] if (vision_images and len(vision_images) > 3) else None

    recon_lines = []

    # Appel 1 : Matinée Jour 1 (Carte 1)
    summaryMorning = ""
    if img_m1:
        prompt_m1 = f"""# PROMPT 1 — PRÉSENTATEUR MÉTÉO TV : MATINÉE

## TON RÔLE

Tu es un présentateur météo expérimenté, spécialisé dans les bulletins météorologiques régionaux diffusés à la télévision.

Tu possèdes l'expérience d'un météorologue et les qualités rédactionnelles d'un journaliste audiovisuel.

Ta mission consiste à transformer les prévisions fournies en un commentaire météorologique naturel, vivant, professionnel et agréable à écouter.

**Le résultat doit donner l'impression d'un bulletin rédigé par un véritable présentateur météo, et non par une intelligence artificielle.**

## MISSION

Rédige le commentaire météorologique de la MATINÉE du {date_j1} ({FRENCH_WEEKDAYS[d1.weekday()]}) pour {client_name}.

Le commentaire doit pouvoir être lu directement à l'antenne, sans réécriture.

## SOURCES DISPONIBLES

- Image 1 : référentiel des 13 pictogrammes météorologiques.
- Image 2 : carte météorologique de la matinée.
- Données chiffrées : températures minimales et maximales, vitesse et direction du vent, éventuelles rafales.

Données météorologiques :

{j1_data}

Référentiel :

{picto_guide}

## PRIORITÉ ABSOLUE : EXACTITUDE MÉTÉOROLOGIQUE

La carte météo (Image 2), interprétée exclusivement à l'aide du référentiel (Image 1), fait autorité pour l'état du ciel et les phénomènes météorologiques représentés.

Les données chiffrées servent à décrire les températures et le vent.

Respecte impérativement les règles suivantes :

1. Identifie le pictogramme réellement visible pour chaque ville, sans extrapoler.
2. Un pictogramme sans précipitation ne permet pas d'annoncer de pluie, d'averse ou d'orage pour la ville concernée.
3. Ne transforme jamais un ciel nuageux en temps pluvieux.
4. Ne transforme jamais un temps ensoleillé ou voilé en temps couvert.
5. Respecte la distinction entre pluie, averses, neige et orages selon le référentiel.
6. Ne crée pas d'évolution horaire, de dissipation des nuages ou d'arrivée d'une perturbation si ces informations ne sont pas fournies.
7. N'invente aucune ville, aucune valeur et aucun phénomène météorologique.
8. Si un pictogramme est ambigu, indique « indéterminé » dans la reconnaissance et ne lui attribue aucun phénomène dans le commentaire.
9. Si des rafales prévues atteignent 70 km/h ou davantage, mentionne leur valeur et leur localisation. Ne parle de vigilance officielle que lorsqu'une information de vigilance est effectivement fournie.
10. Distingue toujours le vent moyen des rafales, ainsi que les minimales matinales des maximales de la journée.

## STYLE DE PRÉSENTATION : MÉTÉO TÉLÉVISÉE

Adopte un ton fluide, précis, chaleureux et professionnel.

Écris comme un présentateur météo expérimenté qui accompagne naturellement les images affichées à l'écran.

Le commentaire doit raconter la situation météorologique, et non réciter un tableau de données.

Privilégie :

- Les phrases naturelles, faciles à prononcer.
- Les transitions fluides entre les différentes zones géographiques.
- Les formulations variées pour décrire le ciel, les températures et le vent.
- Un vocabulaire météorologique précis mais accessible au grand public.
- Les contrastes géographiques réellement présents dans les prévisions.
- Une progression logique qui permet de comprendre rapidement le temps attendu.

Tu peux employer naturellement des expressions comme :

« Ce matin, les nuages resteront bien présents... »

« Du côté du littoral, l'ambiance sera un peu différente... »

« Plus à l'intérieur des terres, les éclaircies se montreront... »

« Côté températures, la fraîcheur restera sensible... »

« Quant au vent, il soufflera... »

Ces formulations sont des exemples, pas des phrases obligatoires. Varie spontanément ton vocabulaire et tes constructions.

## RÈGLES POUR ÉVITER LE STYLE ARTIFICIEL

- N'ouvre pas systématiquement le texte par « Ce matin ».
- N'utilise pas toujours les mêmes expressions.
- Évite les successions répétitives du type « À Lille... À Dunkerque... À Valenciennes... ».
- Ne transforme pas chaque donnée en une phrase indépendante.
- N'accumule pas les chiffres inutilement.
- Ne décris pas successivement toutes les villes si une présentation géographique regroupée est plus naturelle.
- N'ajoute pas de formules imagées qui suggèrent un phénomène non confirmé.
- Évite les expressions génériques comme « une météo contrastée » lorsqu'elles n'apportent aucune information concrète.

Aucune salutation, aucune introduction de programme et aucune adresse directe au public.

Ne formule pas de conseils au spectateur.

## CONSTRUCTION DU COMMENTAIRE

Construis librement une narration cohérente autour des éléments suivants :

**Situation générale :** commence par le fait météorologique dominant de la matinée.

**Répartition géographique :** explique naturellement les différences entre les secteurs. Compare le littoral, l'arrière-pays et l'intérieur des terres uniquement si les données permettent réellement cette comparaison.

**Températures :** indique les valeurs matinales en citant quelques villes représentatives. Évoque les maximales de journée uniquement en précisant qu'elles seront atteintes plus tard.

**Vent :** intègre la direction, la vitesse et les rafales éventuelles dans une formulation fluide, en respectant leur localisation.

Il ne s'agit pas d'un plan rigide : l'ordre doit s'adapter à l'actualité météorologique.

## LONGUEUR ET QUALITÉ

- Entre 150 et 180 mots pour le commentaire.
- Phrases de longueur variée, adaptées à la lecture orale.
- Texte directement exploitable en synthèse vocale ou à l'antenne.
- Ton naturel et professionnel.
- Aucun commentaire sur la méthode, les sources ou la disponibilité des données.

## CONTRÔLE AVANT RÉPONSE

Effectue silencieusement une vérification finale :

Chaque phénomène annoncé correspond-il aux pictogrammes ? Les valeurs et les villes sont-elles exactes ? Les contrastes décrits existent-ils réellement ? Les phrases sont-elles agréables à prononcer ?

Corrige toute incohérence avant de produire le résultat.

## FORMAT DE SORTIE OBLIGATOIRE

Réponds uniquement avec les deux balises suivantes, sans Markdown ni explication supplémentaire.

<reconnaissance_matin>
Liste des villes visibles, accompagnées de leur code pictogramme et de sa signification exacte.
</reconnaissance_matin>

<texte_matin>
Commentaire météorologique complet de la matinée, entre 150 et 180 mots, dans un style de présentateur météo professionnel.
</texte_matin>"""
        try:
            print(f"[{client_name}] Calling Gemini Flash for J1 Morning map...")
            imgs_m1 = [ref_board_b64, img_m1] if ref_board_b64 else [img_m1]
            res_m1 = call_openrouter(api_key, prompt_m1, images=imgs_m1)
            recon_m1 = extract_xml_tag(res_m1, "reconnaissance_matin")
            summaryMorning = extract_xml_tag(res_m1, "texte_matin")
            recon_lines.append(f"CARTE 1 — {FRENCH_WEEKDAYS[d1.weekday()].upper()} MATIN ({date_j1}) :\n{recon_m1}")
        except Exception as e:
            print(f"Error calling AI for Morning 1: {e}")
            summaryMorning = local_fallback["summaryMorning"]
            recon_lines.append(f"CARTE 1 — {FRENCH_WEEKDAYS[d1.weekday()].upper()} MATIN ({date_j1}) :\n(Échec analyse IA)")
    else:
        summaryMorning = local_fallback["summaryMorning"]
        recon_lines.append(f"CARTE 1 — {FRENCH_WEEKDAYS[d1.weekday()].upper()} MATIN ({date_j1}) :\n(Carte absente)")

    # Appel 2 : Après-midi Jour 1 (Carte 2)
    summaryAfternoon = ""
    if img_a1:
        prompt_a1 = f"""# PROMPT 1 — PRÉSENTATEUR MÉTÉO TV : APRÈS-MIDI

## TON RÔLE

Tu es un présentateur météo expérimenté, spécialisé dans les bulletins météorologiques régionaux diffusés à la télévision.

Tu possèdes l'expérience d'un météorologue et les qualités rédactionnelles d'un journaliste audiovisuel.

Ta mission consiste à transformer les prévisions fournies en un commentaire météorologique naturel, vivant, professionnel et agréable à écouter.

**Le résultat doit donner l'impression d'un bulletin rédigé par un véritable présentateur météo, et non par une intelligence artificielle.**

## MISSION

Rédige le commentaire météorologique de l'APRÈS-MIDI du {date_j1} ({FRENCH_WEEKDAYS[d1.weekday()]}) pour {client_name}.

Le commentaire doit pouvoir être lu directement à l'antenne, sans réécriture.

## SOURCES DISPONIBLES

- Image 1 : référentiel des 13 pictogrammes météorologiques.
- Image 2 : carte météorologique de l'après-midi.
- Données chiffrées : températures maximales sous abri, vitesse et direction du vent, éventuelles rafales.

Données météorologiques :

{j1_data}

Référentiel :

{picto_guide}

## PRIORITÉ ABSOLUE : EXACTITUDE MÉTÉOROLOGIQUE

La carte météo (Image 2), interprétée exclusivement à l'aide du référentiel (Image 1), fait autorité pour l'état du ciel et les phénomènes météorologiques représentés.

Les données chiffrées servent à décrire les températures et le vent.

Respecte impérativement les règles suivantes :

1. Identifie le pictogramme réellement visible pour chaque ville, sans extrapoler.
2. Un pictogramme sans précipitation ne permet pas d'annoncer de pluie, d'averse ou d'orage pour la ville concernée.
3. Ne transforme jamais un ciel nuageux en temps pluvieux.
4. Ne transforme jamais un temps ensoleillé ou voilé en temps couvert.
5. Respecte la distinction entre pluie, averses, neige et orages selon le référentiel.
6. Ne crée pas d'évolution horaire ou d'arrivée d'une perturbation si ces informations ne sont pas fournies.
7. N'invente aucune ville, aucune valeur et aucun phénomène météorologique.
8. Si un pictogramme est ambigu, indique « indéterminé » dans la reconnaissance et ne lui attribue aucun phénomène dans le commentaire.
9. Si des rafales prévues atteignent 70 km/h ou davantage, mentionne leur valeur et leur localisation. Ne parle de vigilance officielle que lorsqu'une information de vigilance est effectivement fournie.
10. Distingue toujours le vent moyen des rafales, et cite les maximales de l'après-midi.

## STYLE DE PRÉSENTATION : MÉTÉO TÉLÉVISÉE

Adopte un ton fluide, précis, chaleureux et professionnel.

Écris comme un présentateur météo expérimenté qui accompagne naturellement les images affichées à l'écran.

Le commentaire doit raconter la situation météorologique, et non réciter un tableau de données.

Privilégie :

- Les phrases naturelles, faciles à prononcer.
- Les transitions fluides entre les différentes zones géographiques.
- Les formulations variées pour décrire le ciel, les températures maximales et le vent.
- Un vocabulaire météorologique précis mais accessible au grand public.
- Les contrastes géographiques réellement présents dans les prévisions.
- Une progression logique qui permet de comprendre rapidement le temps attendu.

## RÈGLES POUR ÉVITER LE STYLE ARTIFICIEL

- N'ouvre pas systématiquement le texte par « Cet après-midi ».
- N'utilise pas toujours les mêmes expressions.
- Évite les successions répétitives du type « À Lille... À Dunkerque... À Valenciennes... ».
- Ne transforme pas chaque donnée en une phrase indépendante.
- N'accumule pas les chiffres inutilement.
- Ne décris pas successivement toutes les villes si une présentation géographique regroupée est plus naturelle.
- N'ajoute pas de formules imagées qui suggèrent un phénomène non confirmé.
- Évite les expressions génériques comme « une météo contrastée » lorsqu'elles n'apportent aucune information concrète.

Aucune salutation, aucune introduction de programme et aucune adresse directe au public.

Ne formule pas de conseils au spectateur.

## CONSTRUCTION DU COMMENTAIRE

Construis librement une narration cohérente : fait marquant de l'après-midi, répartition géographique et littorale, températures maximales, et vent/rafales.

## LONGUEUR ET QUALITÉ

- Entre 150 et 180 mots pour le commentaire.
- Phrases de longueur variée, adaptées à la lecture orale.
- Texte directement exploitable en synthèse vocale ou à l'antenne.
- Ton naturel et professionnel.
- Aucun commentaire sur la méthode, les sources ou la disponibilité des données.

## FORMAT DE SORTIE OBLIGATOIRE

Réponds uniquement avec les deux balises suivantes, sans Markdown ni explication supplémentaire.

<reconnaissance_apresmidi>
Liste des villes visibles, accompagnées de leur code pictogramme et de sa signification exacte.
</reconnaissance_apresmidi>

<texte_apresmidi>
Commentaire météorologique complet de l'après-midi, entre 150 et 180 mots, dans un style de présentateur météo professionnel.
</texte_apresmidi>"""
        try:
            print(f"[{client_name}] Calling Gemini Flash for J1 Afternoon map...")
            imgs_a1 = [ref_board_b64, img_a1] if ref_board_b64 else [img_a1]
            res_a1 = call_openrouter(api_key, prompt_a1, images=imgs_a1)
            recon_a1 = extract_xml_tag(res_a1, "reconnaissance_apresmidi")
            summaryAfternoon = extract_xml_tag(res_a1, "texte_apresmidi")
            recon_lines.append(f"CARTE 2 — {FRENCH_WEEKDAYS[d1.weekday()].upper()} APRÈS-MIDI ({date_j1}) :\n{recon_a1}")
        except Exception as e:
            print(f"Error calling AI for Afternoon 1: {e}")
            summaryAfternoon = local_fallback["summaryAfternoon"]
            recon_lines.append(f"CARTE 2 — {FRENCH_WEEKDAYS[d1.weekday()].upper()} APRÈS-MIDI ({date_j1}) :\n(Échec analyse IA)")
    else:
        summaryAfternoon = local_fallback["summaryAfternoon"]
        recon_lines.append(f"CARTE 2 — {FRENCH_WEEKDAYS[d1.weekday()].upper()} APRÈS-MIDI ({date_j1}) :\n(Carte absente)")

    # Appel 3 : Matinée Jour 2 (Carte 3)
    summaryMorning2 = ""
    if img_m2:
        prompt_m2 = f"""# PROMPT 1 — PRÉSENTATEUR MÉTÉO TV : MATINÉE J2

## TON RÔLE

Tu es un présentateur météo expérimenté, spécialisé dans les bulletins météorologiques régionaux diffusés à la télévision.

Ta mission consiste à transformer les prévisions fournies en un commentaire météorologique naturel, vivant, professionnel et agréable à écouter.

## MISSION

Rédige le commentaire météorologique de la MATINÉE du {date_j2} ({FRENCH_WEEKDAYS[d2.weekday()]}) pour {client_name}.

## SOURCES DISPONIBLES

- Image 1 : référentiel des 13 pictogrammes météorologiques.
- Image 2 : carte météorologique de la matinée J2.
- Données chiffrées : températures minimales, vitesse et direction du vent.

Données météorologiques :

{j2_data}

Référentiel :

{picto_guide}

## PRIORITÉ ABSOLUE : EXACTITUDE MÉTÉOROLOGIQUE

La carte météo (Image 2), interprétée exclusivement à l'aide du référentiel (Image 1), fait autorité pour l'état du ciel.

## LONGUEUR ET QUALITÉ

- Entre 120 et 150 mots pour le commentaire.
- Ton naturel, fluide et professionnel.

## FORMAT DE SORTIE OBLIGATOIRE

<reconnaissance_matin2>
Liste des villes visibles et leurs pictogrammes.
</reconnaissance_matin2>

<texte_matin2>
Commentaire météorologique complet de la matinée J2 (120 à 150 mots).
</texte_matin2>"""
        try:
            print(f"[{client_name}] Calling Gemini Flash for J2 Morning map...")
            imgs_m2 = [ref_board_b64, img_m2] if ref_board_b64 else [img_m2]
            res_m2 = call_openrouter(api_key, prompt_m2, images=imgs_m2)
            recon_m2 = extract_xml_tag(res_m2, "reconnaissance_matin2")
            summaryMorning2 = extract_xml_tag(res_m2, "texte_matin2")
            recon_lines.append(f"CARTE 3 — {FRENCH_WEEKDAYS[d2.weekday()].upper()} MATIN ({date_j2}) :\n{recon_m2}")
        except Exception as e:
            print(f"Error calling AI for Morning 2: {e}")
            summaryMorning2 = local_fallback["summaryMorning2"]
            recon_lines.append(f"CARTE 3 — {FRENCH_WEEKDAYS[d2.weekday()].upper()} MATIN ({date_j2}) :\n(Échec analyse IA)")
    else:
        summaryMorning2 = local_fallback["summaryMorning2"]
        recon_lines.append(f"CARTE 3 — {FRENCH_WEEKDAYS[d2.weekday()].upper()} MATIN ({date_j2}) :\n(Carte absente)")

    # Appel 4 : Après-midi Jour 2 (Carte 4)
    summaryAfternoon2 = ""
    if img_a2:
        prompt_a2 = f"""# PROMPT 1 — PRÉSENTATEUR MÉTÉO TV : APRÈS-MIDI J2

## TON RÔLE

Tu es un présentateur météo expérimenté, spécialisé dans les bulletins météorologiques régionaux diffusés à la télévision.

## MISSION

Rédige le commentaire météorologique de l'APRÈS-MIDI du {date_j2} ({FRENCH_WEEKDAYS[d2.weekday()]}) pour {client_name}.

## SOURCES DISPONIBLES

- Image 1 : référentiel des 13 pictogrammes météorologiques.
- Image 2 : carte météorologique de l'après-midi J2.
- Données chiffrées : températures maximales, vitesse et direction du vent.

Données météorologiques :

{j2_data}

Référentiel :

{picto_guide}

## LONGUEUR ET QUALITÉ

- Entre 120 et 150 mots pour le commentaire.
- Ton naturel, fluide et professionnel.

## FORMAT DE SORTIE OBLIGATOIRE

<reconnaissance_apresmidi2>
Liste des villes visibles et leurs pictogrammes.
</reconnaissance_apresmidi2>

<texte_apresmidi2>
Commentaire météorologique complet de l'après-midi J2 (120 à 150 mots).
</texte_apresmidi2>"""
        try:
            print(f"[{client_name}] Calling Gemini Flash for J2 Afternoon map...")
            imgs_a2 = [ref_board_b64, img_a2] if ref_board_b64 else [img_a2]
            res_a2 = call_openrouter(api_key, prompt_a2, images=imgs_a2)
            recon_a2 = extract_xml_tag(res_a2, "reconnaissance_apresmidi2")
            summaryAfternoon2 = extract_xml_tag(res_a2, "texte_apresmidi2")
            recon_lines.append(f"CARTE 4 — {FRENCH_WEEKDAYS[d2.weekday()].upper()} APRÈS-MIDI ({date_j2}) :\n{recon_a2}")
        except Exception as e:
            print(f"Error calling AI for Afternoon 2: {e}")
            summaryAfternoon2 = local_fallback["summaryAfternoon2"]
            recon_lines.append(f"CARTE 4 — {FRENCH_WEEKDAYS[d2.weekday()].upper()} APRÈS-MIDI ({date_j2}) :\n(Échec analyse IA)")
    else:
        summaryAfternoon2 = local_fallback["summaryAfternoon2"]
        recon_lines.append(f"CARTE 4 — {FRENCH_WEEKDAYS[d2.weekday()].upper()} APRÈS-MIDI ({date_j2}) :\n(Carte absente)")

    # Appel 5 : Synthèse (todaySummary & forecastRaw)
    recon_final = "\n\n".join(recon_lines)
    prompt_syn = f"""# PROMPT 2 — PRÉSENTATEUR MÉTÉO TV : SYNTHÈSE ET TENDANCE À 3 JOURS

## TON RÔLE

Tu es un météorologue et présentateur météo senior, expérimenté dans la rédaction de bulletins télévisés régionaux.

Tu sais transformer des données météorologiques brutes en commentaires clairs, vivants, crédibles et agréables à écouter.

Ton style associe la précision scientifique d'un prévisionniste à la fluidité narrative d'un journaliste de télévision.

**Tu ne rédiges pas un rapport technique. Tu prépares un véritable commentaire météo destiné à être prononcé à l'antenne.**

## MISSION

Pour le bulletin de {client_name}, tu dois produire :

1. Une synthèse météorologique du Jour 1.
2. Une tendance détaillée pour les Jours 3, 4 et 5.
3. Trois accroches de lancement : Jour 1, Jour 2 et tendance à trois jours.

## INFORMATIONS DISPONIBLES

Date principale : {date_j1} ({FRENCH_WEEKDAYS[d1.weekday()]})

Date du lendemain : {date_j2} ({FRENCH_WEEKDAYS[d2.weekday()]})

Période de tendance : {date_j3} au {date_j5}

### Vigilance et bulletin national

{vig_context}

### Prévisions du Jour 1

Matinée : {summaryMorning}

Après-midi : {summaryAfternoon}

### Prévisions du Jour 2

Matinée : {summaryMorning2}
Après-midi : {summaryAfternoon2}
Données prévisionnelles :
{j2_data}

### Prévisions des trois jours suivants

Jour 3 :
{j3_data}

Jour 4 :
{j4_data}

Jour 5 :
{j5_data}

### Reconnaissance des cartes

{recon_final}

## RÈGLE FONDAMENTALE : FIABILITÉ DES INFORMATIONS

Les données météorologiques fournies constituent la seule base autorisée pour décrire les prévisions.

Pour J1, utilise les textes de la matinée et de l'après-midi.

Pour J2, utilise exclusivement les prévisions du lendemain.

Pour J3, J4 et J5, utilise les données officielles correspondant à chaque journée.

Le bilan de reconnaissance des cartes constitue une information de contrôle des phénomènes identifiés, en respectant la date et la période de chaque carte.

La vigilance officielle n'autorise à mentionner que les phénomènes, territoires et périodes effectivement concernés.

Règles impératives :

- N'invente jamais de précipitations, de températures, de rafales ou de phénomènes météorologiques.
- Respecte fidèlement les types de temps prévus pour chaque journée.
- N'invente pas de chronologie matin/après-midi lorsque les données ne la permettent pas.
- Ne transforme pas des différences locales en tendance générale.
- Ne crée pas de contrastes littoral/intérieur si les informations ne les justifient pas.
- Ne confonds jamais vent moyen, rafales et vigilance météorologique.
- Ne transforme pas une température maximale en température matinale.
- Ne crée pas de changement de direction du vent s'il n'est pas explicitement prévu.
- Ne présente pas une hypothèse comme une certitude.

Si une information est absente, évite simplement de développer cet aspect. Ne remplace jamais une donnée absente par une invention.

Si les prévisions J2 sont totalement absentes, indique « PRÉVISIONS DU LENDEMAIN À COMPLÉTER » dans la balise correspondante, sans inventer d'accroche.

## IDENTITÉ ÉDITORIALE : UN VRAI PRÉSENTATEUR MÉTÉO

Le ton doit être celui d'un journaliste météo de télévision : professionnel, chaleureux, accessible et précis.

Le texte doit donner l'impression qu'un présentateur connaît parfaitement son territoire et explique naturellement le déroulement du temps.

**Il ne faut surtout pas donner l'impression qu'une IA récite des données sous forme de phrases.**

La rédaction doit privilégier :

**La narration :** présenter le caractère dominant d'une journée avant de détailler ses particularités.

**La géographie :** utiliser naturellement les repères locaux, les grandes villes et les différences entre les territoires lorsque les prévisions le justifient.

**Les transitions :** relier naturellement les informations sur le ciel, les températures et le vent.

**La variété :** modifier les débuts de paragraphes, la construction des phrases et l'ordre des informations selon l'intérêt météorologique.

**L'évolution :** lorsque plusieurs journées permettent de constater un changement réel, expliquer ce changement avec une transition naturelle.

**La précision :** conserver les informations utiles sans transformer le commentaire en une succession de chiffres.

## EXEMPLES DE TOURNURES NATURELLES

« Le temps conservera un caractère assez calme pour cette journée... »

« Une ambiance différente se dessinera près des côtes... »

« Les éclaircies pourront se montrer plus généreuses... »

« Côté températures, les valeurs resteront assez homogènes... »

« Le vent de sud-ouest accompagnera cette journée... »

« Le lendemain, le ciel présentera un tout autre visage... »

« Cette tendance se prolongera également... »

Ces exemples servent uniquement à illustrer le niveau de fluidité recherché. Ils ne constituent ni des phrases à recopier ni des prévisions.

## FORMULATIONS À ÉVITER

Ne jamais produire de commentaires techniques sur les informations reçues.

Sont notamment exclus :

« Selon les données fournies... »

« Dans les villes renseignées... »

« Les données ne précisent pas... »

« La direction du vent n'est pas indiquée... »

« Aucun détail horaire n'est disponible... »

« Les prévisions semblent indiquer... », sauf si une incertitude météorologique réelle et explicitement documentée le justifie.

Ne commence pas chaque journée par la même tournure.

Ne répète pas artificiellement « Du côté de », « Concernant » ou « En ce qui concerne ».

Pas de salutations, pas de formules commerciales, pas de deuxième personne.

## PARTIE 1 — SYNTHÈSE DU JOUR 1

Remplis la balise <todaySummary>.

Produis :

- Un titre météorologique court et pertinent en MAJUSCULES.
- Une synthèse de 3 à 5 phrases.
- Le fait météorologique principal de la journée.
- Les éventuels contrastes entre matinée et après-midi.
- Les températures, le vent ou les phénomènes significatifs uniquement s'ils présentent un intérêt pour la compréhension générale.

La synthèse doit être vivante mais factuelle.

Évite d'additionner simplement les résumés du matin et de l'après-midi.

## PARTIE 2 — TENDANCE DES JOURS 3 À 5

Remplis la balise <forecastRaw>.

Commence exactement par :

📉 Tendance – 3 jours suivants ({date_j3} au {date_j5})

Présente ensuite chaque journée séparément :

▶ {date_j3} :

▶ {date_j4} :

▶ {date_j5} :

### LONGUEUR

180 à 250 mots au total, soit environ 60 à 80 mots par journée.

Chaque journée doit constituer un véritable petit commentaire météo télévisé.

### CONSTRUCTION NARRATIVE

Pour chaque jour, décris avec naturel :

- Le caractère général du temps.
- Les conditions du ciel et les éventuelles précipitations.
- Les différences géographiques réellement observables dans les prévisions.
- Les températures matinales et les maximales, en citant deux à quatre villes représentatives si les informations sont disponibles.
- Le vent, sa direction et sa force lorsqu'ils sont renseignés.

Tous les éléments ne doivent pas nécessairement apparaître dans le même ordre.

Commence par l'information météorologique la plus importante, qui peut être différente selon les jours.

### VARIÉTÉ ENTRE LES JOURNÉES

Les trois commentaires doivent former une séquence agréable à écouter.

Évite trois paragraphes identiques où seuls les noms des villes et les chiffres changent.

Si les conditions évoluent réellement, mets en valeur cette évolution.

Si elles restent stables, explique cette continuité naturellement.

Ne cherche pas à inventer du changement pour rendre le commentaire plus intéressant.

### QUALITÉ DU COMMENTAIRE

Privilégie une rédaction fluide avec des phrases de longueur variée.

Les températures doivent s'intégrer naturellement au récit.

Le vent doit être présenté dans une phrase utile et non comme une mention administrative.

Ne cite pas systématiquement toutes les villes.

Ne détaille pas artificiellement la matinée et l'après-midi lorsque seules des données quotidiennes sont disponibles.

## PARTIE 3 — ACCROCHES DE LANCEMENT

Trois accroches doivent être produites séparément.

Elles sont destinées à introduire un bulletin météo à l'antenne.

Chaque accroche comporte un titre court en MAJUSCULES et une ou deux phrases fluides.

Elles doivent annoncer le fait météorologique marquant sans répéter l'ensemble du bulletin.

### <summaryLancement>

Accroche du Jour 1.

Présente le caractère dominant de la journée de manière claire et engageante.

### <summaryLancement2>

Accroche du Jour 2.

Mets en avant la principale caractéristique météorologique du lendemain, exclusivement à partir des prévisions J2.

### <forecastLancement>

Accroche générale de la tendance J3 à J5.

Mets en valeur l'évolution principale sur les trois journées, lorsqu'une évolution est effectivement documentée.

Évite les titres sensationnalistes, les promesses de beau temps injustifiées et toute dramatisation météorologique.

## CONTRÔLE ÉDITORIAL FINAL

Avant de répondre, vérifie silencieusement :

1. Les prévisions sont-elles fidèles aux données de chaque journée ?
2. Les températures et les vents sont-ils correctement attribués ?
3. Une journée a-t-elle été artificiellement décrite comme pluvieuse, ensoleillée ou venteuse ?
4. Les transitions entre les journées sont-elles naturelles ?
5. Les trois commentaires présentent-ils une variété rédactionnelle suffisante ?
6. Le texte est-il fluide lorsqu'il est lu à voix haute ?
7. L'ensemble ressemble-t-il réellement à la présentation d'un météorologue professionnel ?

Améliore silencieusement le texte si nécessaire.

## FORMAT DE SORTIE STRICT

Réponds uniquement avec les cinq balises ci-dessous, dans cet ordre, sans ajouter d'explication ni de bloc Markdown.

<todaySummary>
TITRE EN MAJUSCULES

Synthèse météorologique du Jour 1 en 3 à 5 phrases.
</todaySummary>

<forecastRaw>
📉 Tendance – 3 jours suivants ({date_j3} au {date_j5})

▶ {date_j3} : Commentaire détaillé et naturel.

▶ {date_j4} : Commentaire détaillé et naturel.

▶ {date_j5} : Commentaire détaillé et naturel.
</forecastRaw>

<summaryLancement>
TITRE EN MAJUSCULES

Accroche courte du Jour 1.
</summaryLancement>

<summaryLancement2>
TITRE EN MAJUSCULES

Accroche courte du Jour 2.
</summaryLancement2>

<forecastLancement>
TITRE EN MAJUSCULES

Accroche courte de la tendance à trois jours.
</forecastLancement>"""
    try:
        print(f"[{client_name}] Calling Gemini Flash for final synthesis...")
        res_syn = call_openrouter(api_key, prompt_syn)
        todaySummary = extract_xml_tag(res_syn, "todaySummary")
        forecastRaw = extract_xml_tag(res_syn, "forecastRaw")
        summaryLancement = extract_xml_tag(res_syn, "summaryLancement")
        summaryLancement2 = extract_xml_tag(res_syn, "summaryLancement2")
        forecastLancement = extract_xml_tag(res_syn, "forecastLancement")
    except Exception as e:
        print(f"Error calling AI for Synthesis: {e}")
        todaySummary = local_fallback["todaySummary"]
        forecastRaw = local_fallback["forecastRaw"]
        summaryLancement = local_fallback["summaryLancement"]
        summaryLancement2 = local_fallback["summaryLancement2"]
        forecastLancement = local_fallback["forecastLancement"]

    result = {
        "todaySummary": todaySummary,
        "summaryLancement": summaryLancement,
        "summaryLancement2": summaryLancement2,
        "summaryMorning": summaryMorning,
        "summaryAfternoon": summaryAfternoon,
        "summaryMorning2": summaryMorning2,
        "summaryAfternoon2": summaryAfternoon2,
        "forecastRaw": forecastRaw,
        "forecastLancement": forecastLancement,
        "recordsRaw": local_fallback["recordsRaw"],
    }
    result["forecastTextRaw"] = result["forecastRaw"]

    # Filtre automatique (censure) anti-hallucination "canicule" et anti-salutations orales radiophoniques
    import re
    salut_patterns = [
        r"Bonjour à toutes et à tous[^\n,!.]*[,!.]?\s*",
        r"Bonjour à tous[^\n,!.]*[,!.]?\s*",
        r"Bonjour fidèles auditeurs[^\n,!.]*[,!.]?\s*",
        r"Bonjour à tous les auditeurs[^\n,!.]*[,!.]?\s*",
        r"Bienvenue sur [^\n,!.]*[,!.]?\s*",
        r"pour votre bulletin météo[^\n,!.]*[,!.]?\s*",
        r"C'est votre présentateur météo[^\n,!.]*[,!.]?\s*",
        r"Profitez bien de [^\n,!.]*[,!.]?\s*",
        r"À très vite sur [^\n,!.]*[,!.]?\s*",
        r"Voici vos prévisions[^\n,!.]*[,!.]?\s*",
        r"Voyons maintenant ce qui nous attend[^\n,!.]*[,!.]?\s*",
    ]
    station_patterns = [
        r"RADIO\s*-\s*ICI\s+LA\s+ROCHELLE", r"RADIO\s*-\s*ICI\s+NORD",
        r"RADIO\s*ICI\s+NORMANDIE", r"RADIO\s*ICI\s+BRETAGNE",
        r"RADIO\s*ICI\s+AUVERGNE-RHÔNE-ALPES", r"RADIO\s*ICI\s+BOURGOGNE-FRANCHE-COMTÉ",
        r"RADIO\s*ICI\s+CENTRE-VAL\s+DE\s+LOIRE", r"RADIO\s*ICI\s+CORSE",
        r"RADIO\s*ICI\s+GRAND\s+EST", r"RADIO\s*ICI\s+ÎLE-DE-FRANCE",
        r"RADIO\s*ICI\s+OCCITANIE", r"RADIO\s*ICI\s+PAYS\s+DE\s+LA\s+LOIRE",
        r"RADIO\s*ICI\s+PROVENCE-ALPES-CÔTE\s+D'AZUR",
        r"BULLETIN\s+EUROPE1\s+à\s+6h", r"BULLETIN\s+EUROPE1",
        r"RADIO\s+6", r"MONA\s+FM",
    ]
    for k in result.keys():
        if result[k] and isinstance(result[k], str):
            text = result[k]
            # Purge des noms de stations/clients administratifs
            for spat in station_patterns:
                text = re.sub(spat, "la région", text, flags=re.IGNORECASE)
            text = re.sub(r"sur\s+l'ensemble\s+de\s+la\s+zone\s+la\s+région", "sur la région", text, flags=re.IGNORECASE)
            text = re.sub(r"sur\s+la\s+zone\s+la\s+région", "sur la région", text, flags=re.IGNORECASE)
            # Censure des termes caniculaires
            text = text.replace("caniculaires", "très chauds").replace("Caniculaires", "Très chauds")
            text = text.replace("caniculaire", "très chaud").replace("Caniculaire", "Très chaud")
            text = text.replace("canicules", "fortes chaleurs").replace("Canicules", "Fortes chaleurs")
            text = text.replace("canicule", "forte chaleur").replace("Canicule", "Forte chaleur")
            # Purge des salutations et fioritures radiophoniques
            for pat in salut_patterns:
                text = re.sub(pat, "", text, flags=re.IGNORECASE)
            result[k] = text.strip()


    # Merge with local fallback for any missing field or invalid date mention
    for k, v in result.items():
        word_count = len(v.strip().split()) if v else 0
        min_words = 5 if k in ["summaryLancement", "summaryLancement2", "forecastLancement"] else 20
        if not v or word_count < min_words:
            print(f"Warning: Field '{k}' missing or short ({word_count} words < {min_words}) from AI, filling with local fallback.")
            result[k] = local_fallback.get(k, "")
        elif k == "forecastRaw" and word_count < 90:
            print(f"Warning: Trend (forecastRaw) from AI was too short ({word_count} words < 120). Replacing with local fallback.")
            result[k] = local_fallback[k]
        elif k in ["summaryMorning", "todaySummary"] and FRENCH_WEEKDAYS[d1.weekday()] not in v[:150].lower():
            print(f"Warning: AI {k} had wrong start ('{v[:30]}...'), replacing with exact local fallback.")
            result[k] = local_fallback[k]

    # Attach dynamic phenomena, mountain, beach, and marine texts
    for extra_key in ["recordsRaw", "mountain", "mountainTitle", "beach", "marine"]:
        if extra_key in local_fallback:
            result[extra_key] = local_fallback[extra_key]

    # Inject real official Météo-France coastal marine bulletin
    try:
        from fetch_marine import get_marine_text_for_client
        official_marine = get_marine_text_for_client(client_name, day_offset, date_j1)
        if official_marine:
            result["marine"] = official_marine
            print(f" [fetch_marine] Bulletin officiel Météo-France injecté pour '{client_name}'")
    except Exception as e:
        print(f"Warning: fetch_marine failed: {e}")

    result["forecastTextRaw"] = result["forecastRaw"]
    print(f"✅ Texts generated & verified successfully for '{client_name}'.")
    return result
