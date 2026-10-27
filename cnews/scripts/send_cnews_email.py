import os
import sys
import subprocess
import smtplib
import base64
import uuid
import datetime
from email.utils import formatdate
import unicodedata

def get_french_date_string(date_obj):
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{weekdays[date_obj.weekday()]} {date_obj.day} {months[date_obj.month - 1]} {date_obj.year}"

def fix_encoding(text):
    if not text:
        return ""
    try:
        return text.encode('latin-1').decode('utf-8')
    except Exception:
        return text

def call_openrouter_llm(system_prompt, user_prompt):
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not openrouter_key:
        print("[LLM] OPENROUTER_API_KEY manquante, pas de résumé IA.")
        return None
    import urllib.request
    import json
    openrouter_key = openrouter_key.replace('\ufeff', '').replace('\ufffe', '').strip()
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {openrouter_key}"
    }
    payload = {
        "model": os.environ.get("OPENROUTER_MODEL", "openai/gpt-6.1-terra"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "reasoning": {"effort": "none"}
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            text = res_data["choices"][0]["message"]["content"]
            return text.replace('\ufeff', '').replace('\ufffe', '').strip()
    except Exception as e:
        print(f"[LLM] Erreur appel OpenRouter : {e}")
        return None

def get_national_forecast():
    import urllib.request
    import re
    url = "https://meteofrance.com/"
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
        bulletins = re.findall(r'<bulletin.*?</bulletin>', html_content, re.DOTALL)
        if not bulletins:
            return None
        b = bulletins[0]
        
        obs = re.search(r'<observation>(.*?)</observation>', b, re.DOTALL)
        date_val = re.search(r'<date>(.*?)</date>', b, re.DOTALL)
        titre = re.search(r'<titre>(.*?)</titre>', b, re.DOTALL)
        temps = re.search(r'<temps>(.*?)</temps>', b, re.DOTALL)
        
        result = {}
        if obs:
            result['observation'] = fix_encoding(obs.group(1).strip())
        if date_val and titre and temps:
            result['date'] = fix_encoding(date_val.group(1).strip())
            result['titre'] = fix_encoding(titre.group(1).strip())
            result['temps'] = fix_encoding(temps.group(1).strip())
        return result
    except Exception as e:
        print(f"[Forecast] Erreur de récupération Météo-France : {e}")
        return None

def send_email(body_text, subject, recipients_str, cartes_dir):
    gmail_email = os.environ.get("GMAIL_EMAIL", "langlet.gregory@gmail.com")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_password:
        print("[SMTP] ERREUR : GMAIL_APP_PASSWORD non configuré. Annulation envoi.")
        return False
        
    gmail_email = gmail_email.replace('\ufeff', '').replace('\ufffe', '').strip()
    gmail_password = gmail_password.replace('\ufeff', '').replace('\ufffe', '').strip()
    
    recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
    sender = gmail_email
    
    # Nettoyage ASCII du sujet pour éviter les rejets SMTP
    clean_subj = unicodedata.normalize('NFKD', subject).encode('ASCII', 'ignore').decode('ASCII')
    
    # Corps HTML en Base64
    body_text = body_text.replace('\ufeff', '').replace('\ufffe', '')
    text_b64 = base64.b64encode(body_text.encode('utf-8')).decode('ascii')
    
    boundary = uuid.uuid4().hex
    
    # Attacher les cartes de vigilance si elles existent (avec superposition du logo pour le mail uniquement)
    vigilance_attachments = []
    vig_files = [
        ("carte_vigilance_france_pictos.jpg", "vigilance_france.jpg"),
        ("carte_vigilance_hdf.jpg", "vigilance_hauts_de_france.jpg")
    ]
    
    # Rechercher le logo Météo Climat Pro
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(scripts_dir, "..", ".."))
    logo_path = None
    logo_names = ["logo meteo climat pro 3.png", "logo.png"]
    for name in logo_names:
        p_script = os.path.join(scripts_dir, "..", "A_CONSERVER_ABSOLUMENT", name)
        p1 = os.path.join(cartes_dir, "A_CONSERVER_ABSOLUMENT", name)
        p2 = os.path.join(cartes_dir, name)
        p3 = os.path.join(r"C:\Users\grego\Desktop\cartes_alertes", name)
        p4 = os.path.join(r"C:\Users\grego\Desktop\cartes_alertes\A_CONSERVER_ABSOLUMENT", name)
        p5 = os.path.join(repo_root, "meteo_cnews_2", name)
        for p_check in [p_script, p1, p2, p3, p4, p5]:
            if os.path.exists(p_check):
                logo_path = p_check
                break
        if logo_path:
            break
    
    for filename, attachment_name in vig_files:
        path = os.path.join(cartes_dir, filename)
        if os.path.exists(path):
            try:
                from PIL import Image
                import io
                
                # Charger la carte de vigilance
                img = Image.open(path)
                
                # Si le logo est disponible, le superposer
                if logo_path and os.path.exists(logo_path):
                    print(f"  -> Superposition du logo sur la pièce jointe {filename} : {logo_path}")
                    logo_img = Image.open(logo_path).convert("RGBA")
                    v_width, v_height = img.size
                    if v_width < v_height: # portrait
                        target_w = 240
                    else: # landscape
                        target_w = 280
                    aspect = logo_img.height / logo_img.width
                    target_h = int(target_w * aspect)
                    logo_resized = logo_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    
                    if v_width < v_height:
                        logo_x = v_width - target_w - 40
                        logo_y = 40
                    else:
                        logo_x = v_width - target_w - 50
                        logo_y = 45
                    img = img.convert("RGBA")
                    img.paste(logo_resized, (logo_x, logo_y), logo_resized)
                
                # Convertir en JPEG dans un buffer
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=95)
                img_data = buf.getvalue()
                
                img_b64 = base64.b64encode(img_data).decode("ascii")
                vigilance_attachments.append((attachment_name, img_b64))
                print(f"  -> Ajout de la vigilance en pièce jointe : {filename}")
            except Exception as e:
                print(f"  -> Impossible de traiter la vigilance {filename} : {e}")
        else:
            print(f"  -> Vigilance {filename} introuvable à {path}")

    # Construction du MIME avec pièces jointes
    mime_parts = [
        f'--{boundary}\r\n',
        f'Content-Type: text/html; charset=utf-8\r\n',
        f'Content-Transfer-Encoding: base64\r\n',
        f'\r\n',
        f'{text_b64}\r\n'
    ]
    
    for att_name, att_b64 in vigilance_attachments:
        mime_parts.append(f'\r\n--{boundary}\r\n')
        mime_parts.append(f'Content-Type: image/jpeg; name="{att_name}"\r\n')
        mime_parts.append(f'Content-Disposition: attachment; filename="{att_name}"\r\n')
        mime_parts.append(f'Content-Transfer-Encoding: base64\r\n')
        mime_parts.append(f'\r\n')
        mime_parts.append(f'{att_b64}\r\n')
        
    mime_parts.append(f'\r\n--{boundary}--\r\n')
    
    raw_message = (
        f'From: Meteo Climat Pro <{sender}>\r\n'
        f'To: {", ".join(recipients)}\r\n'
        f'Reply-To: gregory.langlet@sfr.fr\r\n'
        f'Subject: {clean_subj}\r\n'
        f'Date: {formatdate(localtime=True)}\r\n'
        f'X-Mailer: Python\r\n'
        f'MIME-Version: 1.0\r\n'
        f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n'
        f'\r\n'
        + "".join(mime_parts)
    )
    
    print(f"[SMTP] Envoi via Gmail à {', '.join(recipients)}...")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_email, gmail_password)
            server.sendmail(gmail_email, recipients, raw_message.encode('ascii'))
        print("[SMTP] E-mail CNews envoyé avec succès !")
        return True
    except Exception as e:
        print(f"[SMTP] Erreur d'envoi du mail CNews : {e}")
        return False

def run_command(cmd, cwd):
    print(f"\nExécution : {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Erreur lors de l'exécution de la commande : {e}")
        return False

def main():
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(scripts_dir, "..", ".."))
    cnews_dir = os.path.join(repo_root, "meteo_cnews_2")
    cartes_dir = os.path.join(repo_root, "cartes_alertes")
    if os.environ.get("GITHUB_ACTIONS"):
        os.makedirs(cartes_dir, exist_ok=True)
    else:
        if not os.path.exists(cartes_dir):
            cartes_dir = os.path.expanduser(r"~\Desktop\cartes_alertes")
            os.makedirs(cartes_dir, exist_ok=True)
        
    print("=== ÉTAPE 0.1a : Génération cartes France paysage ===")
    cmd_maps_france_land = ["python", "generate_meteofrance_maps.py", "--zone", "france_pictos", "--days", "5", "--orientation", "landscape", "--patrick", "--temp-highlight"]
    if not run_command(cmd_maps_france_land, cnews_dir):
        sys.exit(1)

    print("=== ÉTAPE 0.1b : Génération cartes France portrait (TikTok) ===")
    cmd_maps_france_port = ["python", "generate_meteofrance_maps.py", "--zone", "france_pictos", "--days", "5", "--orientation", "portrait", "--patrick", "--temp-highlight"]
    if not run_command(cmd_maps_france_port, cnews_dir):
        sys.exit(1)

    print("=== ÉTAPE 0.2a : Génération cartes Hauts-de-France paysage ===")
    cmd_maps_hdf_land = ["python", "generate_meteofrance_maps.py", "--zone", "hdf", "--days", "5", "--orientation", "landscape", "--patrick", "--temp-highlight"]
    if not run_command(cmd_maps_hdf_land, cnews_dir):
        sys.exit(1)

    print("=== ÉTAPE 0.2b : Génération cartes Hauts-de-France portrait (TikTok) ===")
    cmd_maps_hdf_port = ["python", "generate_meteofrance_maps.py", "--zone", "hdf", "--days", "5", "--orientation", "portrait", "--patrick", "--temp-highlight"]
    if not run_command(cmd_maps_hdf_port, cnews_dir):
        sys.exit(1)

    print("=== ÉTAPE 1 : Génération en parallèle des 4 vidéos du Pack Patrick CNews ===")
    
    cmds = [
        # France Paysage
        ["python", "generate_video_bulletin.py", "--zone", "france_pictos", "--days", "5", "--orientation", "landscape", "--patrick", "--skip-maps"],
        # France Portrait (TikTok)
        ["python", "generate_video_bulletin.py", "--zone", "france_pictos", "--days", "5", "--orientation", "portrait", "--patrick", "--skip-maps"],
        # Hauts-de-France Paysage
        ["python", "generate_video_bulletin.py", "--zone", "hdf", "--days", "5", "--orientation", "landscape", "--patrick", "--skip-maps"],
        # Hauts-de-France Portrait (TikTok)
        ["python", "generate_video_bulletin.py", "--zone", "hdf", "--days", "5", "--orientation", "portrait", "--patrick", "--skip-maps"]
    ]
    
    processes = []
    for cmd in cmds:
        p = subprocess.Popen(cmd, cwd=cnews_dir)
        processes.append((cmd, p))
        
    success = True
    for cmd, p in processes:
        exit_code = p.wait()
        if exit_code != 0:
            print(f"Erreur lors de la génération de la vidéo : {' '.join(cmd)}")
            success = False
            
    if not success:
        sys.exit(1)
        
    # Calcul de la date du lendemain (date cible du bulletin)
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    date_suffix = tomorrow.strftime("%Y_%m_%d")

    print("\n=== ÉTAPE 2 : Compression ZIP des 4 vidéos + cartes JPG ===")
    zip_name = f"bulletins_cnews_patrick_{date_suffix}.zip"
    zip_path = os.path.join(cartes_dir, zip_name)

    video_files = [
        f"bulletin_france_pictos_patrick_landscape_{date_suffix}.mp4",
        f"bulletin_france_pictos_patrick_portrait_{date_suffix}.mp4",
        f"bulletin_hdf_patrick_landscape_{date_suffix}.mp4",
        f"bulletin_hdf_patrick_portrait_{date_suffix}.mp4"
    ]

    # Cartes JPG France + HDF (J1 à J5, éphéméride, forêts, vigilance)
    # france_pictos : nommage SANS préfixe de zone (carte_J1_matin.jpg)
    # hdf : nommage AVEC préfixe de zone (carte_hdf_J1_matin.jpg)
    jours = [f"J{i}" for i in range(1, 6)]
    periodes = ["matin", "apresmidi"]
    carte_files = []
    # France (sans préfixe zone)
    for jour in jours:
        for periode in periodes:
            carte_files.append((f"carte_{jour}_{periode}.jpg", f"cartes/france/{jour}_{periode}.jpg"))
    carte_files.append(("carte_france_pictos_ephemeride.jpg", "cartes/france/ephemeride.jpg"))
    carte_files.append(("carte_forets_france_pictos.jpg",     "cartes/france/forets.jpg"))
    carte_files.append(("carte_vigilance_france_pictos.jpg",  "cartes/france/vigilance.jpg"))
    # HDF (avec préfixe zone)
    for jour in jours:
        for periode in periodes:
            carte_files.append((f"carte_hdf_{jour}_{periode}.jpg", f"cartes/hdf/{jour}_{periode}.jpg"))
    carte_files.append(("carte_hdf_ephemeride.jpg", "cartes/hdf/ephemeride.jpg"))
    carte_files.append(("carte_forets_hdf.jpg",     "cartes/hdf/forets.jpg"))
    carte_files.append(("carte_vigilance_hdf.jpg",  "cartes/hdf/vigilance.jpg"))

    import zipfile
    print(f"Création de l'archive {zip_path}...")
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for v_file in video_files:
                v_path = os.path.join(cartes_dir, v_file)
                if os.path.exists(v_path):
                    zipf.write(v_path, arcname=v_file)
                    print(f"  -> Ajouté au ZIP : {v_file}")
                else:
                    print(f"  -> Avertissement : fichier {v_file} introuvable à {v_path}")
            for c_file, c_arcname in carte_files:
                c_path = os.path.join(cartes_dir, c_file)
                if os.path.exists(c_path):
                    zipf.write(c_path, arcname=c_arcname)
                    print(f"  -> Ajouté au ZIP : {c_arcname}")
        print("Archive ZIP créée avec succès.")
    except Exception as e:
        print(f"Erreur lors de la compression ZIP : {e}")
        sys.exit(1)
        
    # Génération du lien de téléchargement (GitHub Releases — pas de limite de taille)
    download_url = f"https://github.com/gregorylanglet59264-byte/meteo-kappa/releases/download/bulletins-patrick-latest/{zip_name}"

    # Calcul des textes pour les réseaux sociaux de Patrick
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Paris")
    except ImportError:
        tz = None

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local = now_utc.astimezone(tz) if tz else now_utc
    
    weekdays_upper = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"]
    months_upper = ["JANVIER", "FÉVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOÛT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DÉCEMBRE"]
    
    today_weekday = weekdays_upper[now_local.weekday()]
    run_hour = f"{now_local.hour}H"
    
    j_plus_5 = tomorrow + datetime.timedelta(days=4)
    
    tomorrow_str_upper = f"{weekdays_upper[tomorrow.weekday()]} {tomorrow.day} {months_upper[tomorrow.month - 1]} {tomorrow.year}"
    j_plus_5_str_upper = f"{weekdays_upper[j_plus_5.weekday()]} {j_plus_5.day} {months_upper[j_plus_5.month - 1]}"
    
    social_text_hdf = f"{today_weekday} {run_hour} - ACTU - PREVISIONS ET ALERTES ---METEO-CLIMAT PRO.  HAUTS DE FRANCE --- CE {tomorrow_str_upper}  ET JUSQU'AU  {j_plus_5_str_upper}  #expertmeteo  #meteo #meteofrance #assurance  #hautsdefrance"
    social_text_france = f"{today_weekday} {run_hour} - ACTU - PREVISIONS ET ALERTES ---METEO-CLIMAT PRO.  FRANCE --- CE {tomorrow_str_upper}  ET JUSQU'AU  {j_plus_5_str_upper}  #expertmeteo  #meteo #meteofrance #assurance  #france"

    # Récupérer et résumer les prévisions nationale et régionale de demain
    print("\n=== ÉTAPE 2.5 : Récupération et résumé des prévisions ===")
    france_summary = "Non disponible"
    hdf_summary = "Non disponible"
    forecast_data = get_national_forecast()
    if forecast_data and 'temps' in forecast_data:
        raw_temps = forecast_data['temps']
        raw_temps = " ".join(raw_temps.split())
        
        # S'il y a une clé API, on fait un résumé propre avec OpenRouter
        if os.environ.get("OPENROUTER_API_KEY"):
            system_prompt = (
                "Tu es un prévisionniste météo senior. Ton rôle est de rédiger deux résumés "
                "de maximum 4 lignes chacun (soit environ 60 à 80 mots par résumé) "
                "des prévisions pour demain : un pour la France entière, et un spécifique "
                "pour la région Hauts-de-France. Base-toi sur le bulletin national fourni pour en extraire "
                "les détails importants (phénomènes, températures, vents).\n\n"
                "RÈGLES CRITIQUES :\n"
                "- N'utilise pas de formatage markdown (pas de **).\n"
                "- Reste factuel, précis et professionnel.\n"
                "- Chaque résumé doit faire strictement un maximum de 4 lignes de texte.\n"
                "- Commencer obligatoirement chaque résumé par une formule d'accroche temporelle comme 'Pour la journée de [Jour]', 'Le temps de ce [Jour]', ou 'La journée de [Jour]' (par exemple : 'Pour la journée de mardi...', 'Le temps de ce mardi...', 'La journée de mardi...').\n"
                "- Retourne uniquement un JSON brut avec la structure suivante (sans bloc de code ```json) :\n"
                "{\n"
                "  \"france\": \"Résumé pour la France (maximum 4 lignes, 60-80 mots)\",\n"
                "  \"hdf\": \"Résumé pour les Hauts-de-France (maximum 4 lignes, 60-80 mots)\"\n"
                "}"
            )
            user_prompt = f"Prévisions brutes de Météo-France :\n{raw_temps}"
            summary_raw = call_openrouter_llm(system_prompt, user_prompt)
            if summary_raw:
                try:
                    import json
                    clean_res = summary_raw.strip().replace("```json", "").replace("```", "").strip()
                    data = json.loads(clean_res)
                    france_summary = data.get("france", raw_temps)
                    hdf_summary = data.get("hdf", "Prévisions régionales à retrouver dans le bulletin vidéo.")
                except Exception as e:
                    print(f"[LLM] Erreur parsing JSON : {e}. Utilisation du fallback.")
                    france_summary = raw_temps
                    hdf_summary = "Prévisions régionales à retrouver dans le bulletin vidéo."
            else:
                france_summary = raw_temps
                hdf_summary = "Prévisions régionales à retrouver dans le bulletin vidéo."
        else:
            france_summary = raw_temps
            hdf_summary = "Prévisions régionales à retrouver dans le bulletin vidéo."
    else:
        print("[Forecast] Impossible d'extraire la prévision nationale de Météo-France.")

    # Corps HTML de l'e-mail avec style épuré et bouton/lien masqué
    email_body = (
        f"<html><body style='font-family: \"Segoe UI\", Tahoma, Geneva, Verdana, sans-serif; font-size: 15px; color: #333; line-height: 1.6;'>"
        f"Bonjour,<br><br>"
        f"Veuillez trouver ci-joint vos bulletins vidéo, veuillez cliquer sur le lien ci-dessous.<br><br>"
        f"👉 <a href='{download_url}' style='color: #1a73e8; font-weight: bold; text-decoration: underline;'>Cliquer sur le lien pour télécharger vos fichiers</a><br><br>"
        
        f"<div style='margin-top: 20px; padding: 15px; border-left: 4px solid #00c6ff; background-color: #f0faff; border-radius: 4px; max-width: 800px; margin-bottom: 20px;'>"
        f"<h3 style='margin-top: 0; color: #007bb6; font-size: 17px;'>📍 HAUTS-DE-FRANCE</h3>"
        f"<p style='margin: 5px 0 2px 0;'><strong>Texte réseaux sociaux :</strong></p>"
        f"<div style='background: #fff; padding: 10px; border: 1px solid #ddd; margin-bottom: 12px; font-family: monospace; font-size: 13px; border-radius: 3px; word-break: break-all; white-space: pre-wrap;'>{social_text_hdf}</div>"
        f"<p style='margin: 5px 0 2px 0;'><strong>Résumé des prévisions du lendemain :</strong></p>"
        f"<div style='background: #fffbeb; padding: 10px; border: 1px solid #ffeeba; font-size: 14px; text-align: justify; line-height: 1.5; border-radius: 3px; color: #856404;'>{hdf_summary}</div>"
        f"</div>"
        
        f"<div style='margin-top: 20px; padding: 15px; border-left: 4px solid #66bb6a; background-color: #f1f9f1; border-radius: 4px; max-width: 800px;'>"
        f"<h3 style='margin-top: 0; color: #2e7d32; font-size: 17px;'>📍 FRANCE</h3>"
        f"<p style='margin: 5px 0 2px 0;'><strong>Texte réseaux sociaux :</strong></p>"
        f"<div style='background: #fff; padding: 10px; border: 1px solid #ddd; margin-bottom: 12px; font-family: monospace; font-size: 13px; border-radius: 3px; word-break: break-all; white-space: pre-wrap;'>{social_text_france}</div>"
        f"<p style='margin: 5px 0 2px 0;'><strong>Résumé des prévisions du lendemain :</strong></p>"
        f"<div style='background: #fffbeb; padding: 10px; border: 1px solid #ffeeba; font-size: 14px; text-align: justify; line-height: 1.5; border-radius: 3px; color: #856404;'>{france_summary}</div>"
        f"</div><br>"
        
        f"Cordialement,<br>"
        f"L'automatisation Météo CNews"
        f"</body></html>"
    )
        
    print("\n=== ÉTAPE 3 : Envoi de l'e-mail ===")
    subject = f"Dossier du {get_french_date_string(tomorrow)}"
    
    # Gestion du mode test : si argument "test_mode" ou variable d'env GHA active
    test_mode = os.environ.get("TEST_MODE", "false").lower() in ["true", "1", "yes"]
    if len(sys.argv) > 1 and sys.argv[1] == "--test-mode":
        test_mode = True
        
    if test_mode:
        recipients = "gregory.langlet@sfr.fr, langlet.gregory@gmail.com"
        print("[MODE TEST ACTIVE] Envoi restreint à Grégory uniquement.")
    else:
        recipients = "gregory.langlet@sfr.fr, langlet.gregory@gmail.com, patrick.marliere@wanadoo.fr"
        print("[MODE PRODUCTION] Envoi à Grégory et Patrick.")
        
    send_email(email_body, subject, recipients, cartes_dir)

if __name__ == "__main__":
    main()
