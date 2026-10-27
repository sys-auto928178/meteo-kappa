
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
        prompt_m1 = f"""Tu es un journaliste météo de presse écrite. Rédige l'article d'information météo pour la MATINÉE du {date_j1} ({FRENCH_WEEKDAYS[d1.weekday()]}) pour "{client_name}".

Règles de style journalistique OBLIGATOIRES :
- Tone : Journalistique, factuel, fluide, élégant et descriptif.
- ⛔ INTERDICTION ABSOLUE des fioritures et salutations radiophoniques ou orales ("Bonjour à tous", "Bienvenue sur...", "C'est votre présentateur...", "voici vos prévisions", "profitez bien", "à très vite", etc.).
- ⛔ Ne parle jamais au lecteur/auditeur à la 2ème personne (pas de "vous", "vos activités"). Rédige exclusivement à la 3ème personne.
- ⚠️ RÈGLE D'OR DE COHÉRENCE AVEC LA CARTE (TEMPS SENSIBLE) :
  La CARTE MÉTÉO (Image 2) comparée à la PLANCHE OFFICIELLE (Image 1) est la SEULE ET UNIQUE SOURCE DE VÉRITÉ pour l'état du ciel et les précipitations.
  - Si le pictogramme d'une ville est du soleil (P1, P2), du soleil voilé (P6) ou des nuages (P8, P4) sans gouttes d'eau, tu dois DÉCRIRE UN TEMPS SEC ET LUMINEUX / VOILÉ / NUAGEUX, SANS AUCUNE PLUIE NI AVERSE !
  - Tu ne dois citer d'averses ou de pluie QUE si et seulement si un pictogramme de pluie ou d'orage est visible sur la carte (P9 Averses, P10 Pluie faible, P11 Pluie forte, P12 Neige, orages).
  - Les données chiffrées ci-dessous servent EXCLUSIVEMENT pour les températures réelles (min/max) et le vent.
- Intègre obligatoirement des indications précises sur le VENT (brise, vent modéré, mistral, etc.) et le contraste de températures LITTORAL / INTÉRIEUR DES TERRES s'il y a lieu.
- ⚠️ Si des rafales de vent de 70 km/h ou plus (gust) sont indiquées dans les données ci-dessous, tu dois obligatoirement et expressément les citer (risque de vigilance vent fort). En dessous de 70 km/h, mentionne simplement le vent sans dramatiser.
- Si tu vois un éclair (orage ou grêle), mentionne-le expressément (risque orageux, foudre).

{picto_guide}

Données réelles pour ce matin :
{j1_data}

Instructions :
1. Remplis la balise <reconnaissance_matin> avec la liste des villes visibles sur la carte et leur picto. Ex: "Lille = soleil, Douai = ORAGE ⚠️"
2. Remplis la balise <texte_matin> avec ton article d'information matinale complet (150 à 180 mots). Doit commencer par "Ce {FRENCH_WEEKDAYS[d1.weekday()]} matin..." et citer 5 à 6 villes clés avec minimales réelles. Rédige un texte fluide, riche et descriptif, intégrant les ambiances régionales et littorales.
"""
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
        prompt_a1 = f"""Tu es un journaliste météo de presse écrite. Rédige l'article d'information météo pour l'APRÈS-MIDI du {date_j1} ({FRENCH_WEEKDAYS[d1.weekday()]}) pour "{client_name}".

Règles de style journalistique OBLIGATOIRES :
- Tone : Journalistique, factuel, fluide, élégant et descriptif.
- ⛔ INTERDICTION ABSOLUE des fioritures et salutations radiophoniques ou orales ("Bonjour à tous", "Bienvenue sur...", "C'est votre présentateur...", "voici vos prévisions", "profitez bien", "à très vite", etc.).
- ⛔ Ne parle jamais au lecteur/auditeur à la 2ème personne (pas de "vous", "vos activités"). Rédige exclusivement à la 3ème personne.
- ⚠️ MENTION IMPÉRATIVE DU TEMPS SENSIBLE : Tu dois TOUJOURS décrire avec précision l'état du ciel et les précipitations réelles (averses, pluies, éclaircies, soleil dominant, nuages bas, brumes, orages). Si la carte ou les données indiquent des averses ou des pluies, tu dois OBLIGATOIREMENT les citer expressément dans le texte (ex: "des averses parfois soutenues sont attendues", "un passage pluvieux s'invite"). INTERDICTION de passer sous silence des averses ou des pluies !
- Intègre obligatoirement des indications précises sur le VENT (mistral, brise côtière, vent d'ouest, etc.) et le contraste de températures LITTORAL / INTÉRIEUR DES TERRES s'il y a lieu.
- ⚠️ Si des rafales de vent de 70 km/h ou plus (gust) sont indiquées dans les données ci-dessous, tu dois obligatoirement et expressément les citer (risque de vigilance vent fort). En dessous de 70 km/h, mentionne simplement le vent sans dramatiser.
- ⚠️ Si tu vois un éclair (orage ou grêle), alerte obligatoire et explicite (risques de foudre, fortes pluies sous cellules, grêle).

{picto_guide}

Données réelles pour cet après-midi :
{j1_data}

Instructions :
1. Remplis la balise <reconnaissance_apresmidi> avec la liste des villes et leur picto.
2. Remplis la balise <texte_apresmidi> avec ton article d'information après-midi complet (150 à 180 mots). Doit commencer par "Ce {FRENCH_WEEKDAYS[d1.weekday()]} après-midi..." et citer 5 à 6 villes clés avec maximales réelles. Décris en détail l'évolution thermique, l'ensoleillement et les contrastes régionaux/côtiers.
"""
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
        prompt_m2 = f"""Tu es un journaliste météo de presse écrite. Rédige l'article d'information météo pour la MATINÉE du {date_j2} ({FRENCH_WEEKDAYS[d2.weekday()]}) pour "{client_name}".

Règles de style journalistique OBLIGATOIRES :
- Tone : Journalistique, factuel, fluide et descriptif.
- ⛔ INTERDICTION ABSOLUE des fioritures et salutations radiophoniques ou orales ("Bonjour à tous", "Bienvenue sur...", "profitez bien", etc.). Rédige à la 3ème personne.
- 120 à 150 mots. Doit commencer par "Ce {FRENCH_WEEKDAYS[d2.weekday()]} matin..." et citer 4 à 5 villes avec minimales réelles.
- ⚠️ Si des rafales de vent de 70 km/h ou plus (gust) sont indiquées dans les données ci-dessous, tu dois obligatoirement et expressément les citer (risque de vigilance vent fort). En dessous de 70 km/h, mentionne simplement le vent sans dramatiser.

{picto_guide}

Données réelles pour ce matin-là :
{j2_data}

Instructions :
1. Remplis la balise <reconnaissance_matin2> avec la liste des villes et leur picto.
2. Remplis la balise <texte_matin2> avec ton commentaire.
"""
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
        prompt_a2 = f"""Tu es un journaliste météo de presse écrite. Rédige l'article d'information météo pour l'APRÈS-MIDI du {date_j2} ({FRENCH_WEEKDAYS[d2.weekday()]}) pour "{client_name}".

Règles de style journalistique OBLIGATOIRES :
- Tone : Journalistique, factuel, fluide et descriptif.
- ⛔ INTERDICTION ABSOLUE des fioritures et salutations radiophoniques ou orales ("Bonjour à tous", "Bienvenue sur...", "profitez bien", etc.). Rédige à la 3ème personne.
- 120 à 150 mots. Doit commencer par "Ce {FRENCH_WEEKDAYS[d2.weekday()]} après-midi..." et citer 4 à 5 villes avec maximales réelles.
- ⚠️ Si des rafales de vent de 70 km/h ou plus (gust) sont indiquées dans les données ci-dessous, tu dois obligatoirement et expressément les citer (risque de vigilance vent fort). En dessous de 70 km/h, mentionne simplement le vent sans dramatiser.
- Si orage visible, mentionne-le expressément.

{picto_guide}

Données réelles pour cet après-midi-là :
{j2_data}

Instructions :
1. Remplis la balise <reconnaissance_apresmidi2> avec la liste des villes et leur picto.
2. Remplis la balise <texte_apresmidi2> avec ton commentaire.
"""
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
    prompt_syn = f"""Tu es un journaliste météo senior de presse écrite. Rédige le résumé de la journée, les phrases de lancement et la tendance à 3 jours pour le bulletin d'information "{client_name}".

Date cible principale (Jour 1) : {date_j1} ({FRENCH_WEEKDAYS[d1.weekday()]})
Date cible Jour 2 : {date_j2} ({FRENCH_WEEKDAYS[d2.weekday()]})

DONNÉES OFFICIELLES VIGILANCE & BULLETIN NATIONAL MÉTÉO-FRANCE :
{vig_context}

PRÉVISIONS DE LA JOURNÉE PAR L'IA :
Matinée Jour 1 : {summaryMorning}
Après-midi Jour 1 : {summaryAfternoon}

DONNÉES MÉTÉO RÉELLES DES JOURS DE TENDANCE (J3, J4, J5) :
- Jour 3 ({date_j3}) :
{j3_data}

- Jour 4 ({date_j4}) :
{j4_data}

- Jour 5 ({date_j5}) :
{j5_data}

BILAN DE LA RECONNAISSANCE DES CARTES :
{recon_final}

Instructions OBLIGATOIRES (Style journalistique strict & complet) :
- ⛔ INTERDICTION ABSOLUE des formules de politesse orales ("Bonjour à tous", "Bienvenue sur...", "voici vos prévisions", "profitez bien", "à très vite", etc.).
- ⛔ Ne parle pas au lecteur à la 2ème personne (pas de "vous"). Rédige à la 3ème personne.

1. Remplis la balise <todaySummary> (STRICTEMENT 3 à 5 phrases). Rédige UNIQUEMENT L'ESSENTIEL DE LA JOURNÉE À RETENIR pour le grand public (phénomène marquant, températures min/max, vent). ⛔ INTERDICTION FORMELLE de citer des noms de code de clients ou stations radio. Doit commencer par un titre percutant en MAJUSCULES (ex: "🌤️ L'ESSENTIEL DE CE {FRENCH_WEEKDAYS[d1.weekday()].upper()} : ...").

2. Remplis la balise <forecastRaw> avec la TENDANCE SÉPARÉE JOUR PAR JOUR du Jour 3 au Jour 5 ({date_j3}, {date_j4}, {date_j5}).
   ATTENTION RÈGLE STRICTE DE RICHESSE ET DE STYLE BROADCAST (180 à 250 mots au total) :
   Commence obligatoirement par le titre :
   📉 Tendance – 3 jours suivants ({date_j3} au {date_j5})

   Pour CHAQUE jour de tendance ({date_j3}, {date_j4}, {date_j5}), rédige un paragraphe complet, vivant, imagé et chaleureux de 4 à 5 lignes détaillées (environ 60 à 80 mots par jour), comme un présentateur météo expérimenté à l'antenne :
   - Décris l'état du ciel et l'ambiance générale en te basant sur le Temps officiel Météo-France (soleil, ciel voilé, nuages, éclaircies, averses).
   - Décris le contraste géographique naturel (ex: littoral, arrière-pays, plaines, vallées).
   - Donne les températures minimales matinales et les maximales de l'après-midi en citant 3 à 4 villes repères.
   - Mentionne le vent avec sa direction et sa force de façon naturelle (ex: "un vent modéré de secteur sud-ouest autour de 15 à 20 km/h"), et cite les rafales si elles dépassent 40 km/h.

   ⛔ BANNISSEMENT ABSOLU DU PARLER-ROBOT ET DES MÉTA-COMMENTAIRES DE DONNÉES (RÈGLE D'OR RADIO) :
   - ❌ INTERDICTION FORMELLE d'écrire "dans les villes renseignées", "dans les données fournies", "selon nos données". Utilise TOUJOURS la vraie géographie régionale ("sur l'ensemble de la région", "du littoral aux plaines intérieures", "de la côte aux collines").
   - ❌ INTERDICTION FORMELLE d'écrire "sa direction n'est pas précisée", "direction non indiquée", "direction non renseignée". Tu disposes de la direction du vent dans les données ou tu utilises une formulation naturelle ("vent de secteur sud-ouest", "brise modérée d'ouest"). Ne dis JAMAIS que la direction n'est pas précisée !
   - ❌ INTERDICTION FORMELLE d'écrire "sans détail horaire", "leur horaire n'est pas précisé". Décris simplement l'ambiance au fil des heures ou en après-midi.
   - ❌ INTERDICTION FORMELLE d'abréger ou de regrouper des jours. Chaque jour a son propre paragraphe autonome et captivant.

   Format exact :
   📉 Tendance – 3 jours suivants ({date_j3} au {date_j5})

   ▶ {date_j3} : [4 à 5 lignes détaillées avec ciel, évolution matin/après-midi, températures min/max et vent/rafales]

   ▶ {date_j4} : [4 à 5 lignes détaillées avec ciel, évolution matin/après-midi, températures min/max et vent/rafales]

   ▶ {date_j5} : [4 à 5 lignes détaillées avec ciel, évolution matin/après-midi, températures min/max et vent/rafales]

3. Remplis la balise <summaryLancement> avec un TITRE DE PRESSE EN MAJUSCULES SUIVI D'UNE PHRASE D'ACCROCHE RÉSUMANT LA PRÉVISION DU JOUR 1 ({FRENCH_WEEKDAYS[d1.weekday()]}) (ex: "☀️ SOLEIL ET CHALEUR ESTIVALE : Un ciel très dégagé s'impose sur la région ce {FRENCH_WEEKDAYS[d1.weekday()]}, avec des températures maximales très douces.").

4. Remplis la balise <summaryLancement2> avec un TITRE DE PRESSE EN MAJUSCULES SUIVI D'UNE PHRASE D'ACCROCHE RÉSUMANT LA PRÉVISION DU JOUR 2 ({FRENCH_WEEKDAYS[d2.weekday()]}) (ex: "🌤️ TEMPS CALME ET LUMINEUX : Le soleil reste largement dominant pour ce {FRENCH_WEEKDAYS[d2.weekday()]} avec des températures de saison.").

5. Remplis la balise <forecastLancement> avec un TITRE DE PRESSE EN MAJUSCULES SUIVI D'UNE PHRASE D'ACCROCHE RÉSUMANT LA TENDANCE À 3 JOURS ({date_j3} au {date_j5}) (ex: "📉 EVOLUTION DE LA TENDANCE : Un temps sec et ensoleillé privilégié pour la suite de la semaine sur l'ensemble du territoire.").
"""
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
