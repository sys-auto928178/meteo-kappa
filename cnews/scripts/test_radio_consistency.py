"""
Test de non-régression automatique pour la cohérence des bulletins radio Météo Kappa.
Vérifie la parité géographique (Nord 59 / Pas-de-Calais 62, Charente-Maritime 17 / Charente 16),
la présence des blocs obligatoires et la conformité des tendances sur l'ensemble des 6 stations.
"""
import os
import json

def test_bulletins_consistency():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    json_path = os.path.join(project_root, "AUTOMATISATION.json")
    
    assert os.path.exists(json_path), f"Fichier introuvable: {json_path}"
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    clients = {c["name"]: c for c in data["clients"]}

    # 1. Présence des 6 stations obligatoires
    expected_clients = [
        "BULLETIN EUROPE1 à 6h",
        "RADIO - ICI NORD",
        "RADIO - ICI LA ROCHELLE",
        "RADIO 6",
        "MONA FM",
        "RADIO ICI NORMANDIE"
    ]
    for name in expected_clients:
        assert name in clients, f"Client manquant : {name}"
        form = clients[name].get("form", {})
        assert form.get("summaryMorning"), f"summaryMorning manquant pour {name}"
        assert form.get("summaryAfternoon"), f"summaryAfternoon manquant pour {name}"
        assert form.get("forecastRaw"), f"forecastRaw manquant pour {name}"
        assert "▶" in form.get("forecastRaw", ""), f"Format de tendance invalide pour {name}"

    # 2. Parité géographique ICI Nord (10 villes 59 + 10 villes 62)
    nord = clients["RADIO - ICI NORD"]
    assert nord.get("region") == "hdf", f"Région incorrecte pour ICI Nord: {nord.get('region')}"
    cities_59 = [c for c in nord["cities"] if c.get("dept") == "59"]
    cities_62 = [c for c in nord["cities"] if c.get("dept") == "62"]
    assert len(cities_59) == 10, f"Erreur parité 59 : attendu 10, obtenu {len(cities_59)}"
    assert len(cities_62) == 10, f"Erreur parité 62 : attendu 10, obtenu {len(cities_62)}"

    # 3. Équilibre géographique ICI La Rochelle (10 villes 17 + 6 villes 16)
    rochelle = clients["RADIO - ICI LA ROCHELLE"]
    assert rochelle.get("region") == "naq", f"Région incorrecte pour ICI La Rochelle: {rochelle.get('region')}"
    cities_17 = [c for c in rochelle["cities"] if c.get("dept") == "17"]
    cities_16 = [c for c in rochelle["cities"] if c.get("dept") == "16"]
    assert len(cities_17) == 10, f"Erreur équilibre 17 : attendu 10, obtenu {len(cities_17)}"
    assert len(cities_16) == 6, f"Erreur équilibre 16 : attendu 6, obtenu {len(cities_16)}"

    # 4. Modules littoral & marin sur les stations côtières
    for coastal_station in ["RADIO - ICI NORD", "RADIO - ICI LA ROCHELLE", "RADIO 6", "RADIO ICI NORMANDIE"]:
        form = clients[coastal_station]["form"]
        assert form.get("beach"), f"Module plage manquant pour {coastal_station}"
        assert form.get("marine"), f"Module marine manquant pour {coastal_station}"

    print("✅ Test de cohérence Météo Kappa : 100% SUCCÈS sur les 6 stations.")

if __name__ == "__main__":
    test_bulletins_consistency()
