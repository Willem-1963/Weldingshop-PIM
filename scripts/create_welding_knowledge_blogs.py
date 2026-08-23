from __future__ import annotations

import argparse
import json
import mimetypes
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from app.shopify.client import ShopifyClient
from app.shopify.sync import upload_test_product_family
from app.suppliers.hub import get_supplier_product


BLOG_HANDLE = "news"
ASSET_DIR = Path(__file__).resolve().parents[1] / "data" / "blog_assets"


BLOGS = [
    {
        "handle": "lasposities-pa-pg-uitgelegd",
        "title": "Lasposities PA tot en met PG uitgelegd",
        "summary": "Praktische uitleg van de lasposities PA, PB, PC, PD, PE, PF en PG, inclusief smeltbadcontrole en startpunten voor de machine-instelling.",
        "image": "lasposities-pa-pg.png",
        "alt": "Cartoon van een lasser die in verschillende lasposities werkt",
        "tags": ["Lasposities", "Lastechniek", "MIG/MAG", "TIG", "Elektrode"],
        "body": """
<h2>Lasposities bepalen meer dan alleen uw werkhouding</h2>
<p>Dezelfde lasverbinding kan in de vlakke positie rustig lopen en verticaal of bovenhands ineens veel lastiger worden. De zwaartekracht trekt aan het vloeibare smeltbad, de warmte blijft anders in het werkstuk en de lasser moet booglengte, voortloopsnelheid en toortshoek aanpassen. De aanduidingen PA tot en met PG maken duidelijk in welke positie wordt gelast.</p>
<p>De positiecodes worden gebruikt bij toevoegmaterialen, lasmethodebeschrijvingen en kwalificaties. Ze zeggen niet automatisch welke stroomsterkte goed is. Materiaal, dikte, lasproces, draaddiameter en het datablad van het toevoegmateriaal blijven bepalend.</p>
<h2>Wat betekenen PA tot en met PG?</h2>
<h3>PA – vlak lassen</h3><p>De las ligt aan de bovenzijde en het smeltbad wordt door de zwaartekracht ondersteund. Dit is meestal de eenvoudigste en productiefste positie en vormt een logisch uitgangspunt voor de machine-instelling.</p>
<h3>PB – horizontaal-verticale hoeklas</h3><p>De hoeklas loopt horizontaal tussen een horizontaal en verticaal deel. Richt de boog zo dat beide flanken goed worden meegenomen en voorkom dat het bad naar de onderste plaat zakt.</p>
<h3>PC – horizontale stompe las</h3><p>De lasas loopt horizontaal in een verticale plaat. De bovenzijde kan snel ondersnijden terwijl aan de onderzijde te veel materiaal ontstaat. Een beheerst, klein smeltbad helpt.</p>
<h3>PD – bovenhandse hoeklas</h3><p>De hoeklas bevindt zich boven de lasser. Werk met volledige bescherming, houd de boog kort en voorkom een te groot vloeibaar bad.</p>
<h3>PE – bovenhandse stompe las</h3><p>Dit is een veeleisende positie. Goede voorbereiding, een korte boog, beperkte warmte-inbreng en gecontroleerde voortloopsnelheid zijn belangrijk.</p>
<h3>PF – verticaal omhoog</h3><p>Bij verticaal omhoog wordt tegen de zwaartekracht in opgebouwd. Deze richting wordt vaak gekozen wanneer inbranding en laagopbouw belangrijk zijn. Houd het smeltbad klein en geef de flanken voldoende tijd.</p>
<h3>PG – verticaal omlaag</h3><p>Verticaal omlaag kan snel werken op dun materiaal, maar geeft bij verkeerd gebruik gemakkelijk geringe inbranding. Gebruik deze richting alleen wanneer toevoegmateriaal, verbinding en lasmethode daarvoor geschikt zijn.</p>
<h2>Moet het amperage omhoog of omlaag?</h2>
<p>Gebruik de vlakke instelling als vertrekpunt. Bij verticaal en bovenhands lassen wordt de warmte-inbreng vaak verlaagd om het smeltbad beheersbaar te houden. Bij MIG/MAG kan een verlaging van spanning en draadaanvoer met ongeveer 10 tot 15 procent een bruikbare eerste proefinstelling zijn. Bij elektrode en TIG wordt vaak eveneens lager begonnen dan in PA.</p>
<p>Dit is geen vaste receptuur. Een te lage instelling veroorzaakt bindingsfouten; een te hoge instelling maakt het bad onbeheersbaar. Maak een proeflas en volg altijd het productdatablad en, waar van toepassing, de WPS.</p>
<h2>Praktische controle vóór het lassen</h2>
<ul><li>Controleer of het toevoegmateriaal voor de gewenste positie is goedgekeurd.</li><li>Begin met een schoon en correct voorbereid werkstuk.</li><li>Houd bij moeilijke posities het smeltbad kleiner.</li><li>Pas steeds één instelling tegelijk aan.</li><li>Controleer inbranding, flankbinding en lasprofiel op een proefstuk.</li></ul>
""",
    },
    {
        "handle": "putcorrosie-en-pren-bij-rvs",
        "title": "Putcorrosie en PREN bij RVS uitgelegd",
        "summary": "Wat putcorrosie is, wat een PREN-waarde aangeeft en waarom materiaal, lasmetaal en uitvoering samen de corrosiebestendigheid bepalen.",
        "image": "putcorrosie-pren.png",
        "alt": "Cartoon van een inspectie van putcorrosie in roestvast staal",
        "tags": ["RVS", "Duplex", "Putcorrosie", "PREN", "Corrosie"],
        "body": """
<h2>Een klein putje kan een groot probleem worden</h2>
<p>Roestvast staal is corrosiebestendig dankzij een dunne passieve laag. In een agressieve omgeving kan die bescherming lokaal doorbreken. Er ontstaat dan putcorrosie: een kleine opening aan het oppervlak die zich dieper in het materiaal kan voortzetten. Chloriden, temperatuur, stilstaand medium, oppervlakteconditie en lasverkleuring spelen daarbij een belangrijke rol.</p>
<h2>Wat betekent PREN?</h2>
<p>PREN staat voor Pitting Resistance Equivalent Number. Het is een rekenkundige indicatie van de weerstand van een RVS-legering tegen putcorrosie. De waarde wordt vooral bepaald door het gehalte chroom, molybdeen en stikstof. Een hogere waarde wijst doorgaans op een grotere potentiële weerstand tegen lokale aantasting.</p>
<p>PREN ≥ 35 betekent dat een legering of lasmetaal een verhoogde weerstand tegen putcorrosie kan bieden. Het is geen garantie dat een constructie in iedere omgeving probleemloos blijft. De werkelijke prestatie hangt ook af van ontwerp, temperatuur, medium, oppervlakteafwerking en laskwaliteit.</p>
<h2>Waarom het lasmetaal moet passen</h2>
<p>Een verbinding is niet automatisch even corrosiebestendig als het basismateriaal. Een verkeerde toevoeglegering, te hoge warmte-inbreng, onvoldoende gasbescherming of achtergebleven warmteverkleuring kan de lokale weerstand verlagen. Bij duplex RVS is bovendien de balans tussen ferriet en austeniet belangrijk.</p>
<h2>PREN vergelijken zonder schijnzekerheid</h2>
<ul><li>Vergelijk waarden alleen wanneer dezelfde rekenmethode wordt gebruikt.</li><li>Kijk naar basismateriaal én lasmetaal.</li><li>Beoordeel de werkelijke bedrijfsomgeving.</li><li>Verwijder lasverkleuring en verontreiniging volgens de voorgeschreven methode.</li><li>Volg materiaalcertificaat, productdatablad en lasmethode.</li></ul>
<h2>Wanneer extra advies nodig is</h2>
<p>Bij zeewater, chemische installaties, hoge temperaturen, spleten of stilstaande chloridehoudende vloeistoffen is alleen een PREN-getal onvoldoende. Laat materiaalkeuze en lasprocedure dan beoordelen op basis van de volledige toepassing.</p>
""",
    },
    {
        "handle": "natteigenschappen-en-mechanische-eigenschappen-lasmetaal",
        "title": "Natteigenschappen en mechanische eigenschappen van lasmetaal",
        "summary": "Praktische uitleg van natteigenschappen, treksterkte, vloeigrens, rek en kerftaaiheid bij de keuze van lastoevoegmateriaal.",
        "image": "mechanische-eigenschappen-lasmetaal.png",
        "alt": "Cartoon van mechanische beproeving van gelaste proefstukken",
        "tags": ["Lasmetaal", "Natteigenschappen", "Treksterkte", "Kerftaaiheid"],
        "body": """
<h2>Natteigenschappen zeggen iets anders dan sterkte</h2>
<p>Goede natteigenschappen betekenen dat het vloeibare lasmetaal zich gemakkelijk over de flanken verspreidt en vloeiend aansluit op het basismateriaal. Dat helpt bij een rustig lasbeeld en een gelijkmatig lasprofiel. Het zegt niet automatisch dat de verbinding sterker of taaier is.</p>
<p>Natteigenschappen worden beïnvloed door toevoegmateriaal, beschermgas, oppervlaktespanning, stroom en spanning, voortloopsnelheid, toortshoek en de reinheid van het werkstuk. Een mooi vloeiende las kan nog steeds onvoldoende inbranding of bindingsfouten bevatten.</p>
<h2>Treksterkte en vloeigrens</h2>
<p>De treksterkte geeft aan welke maximale trekspanning het materiaal kan weerstaan voordat het breekt. De vloeigrens geeft aan wanneer blijvende vervorming begint. Voor een verantwoorde keuze moeten lasmetaal, basismateriaal en constructie-eis bij elkaar passen.</p>
<h2>Rek en vervormbaarheid</h2>
<p>Rek is een maat voor de plastische vervorming vóór breuk. Een hoge sterkte zonder voldoende vervormbaarheid kan in een dynamisch belaste of star opgesloten verbinding ongunstig zijn.</p>
<h2>Kerftaaiheid</h2>
<p>Kerftaaiheid beschrijft hoeveel slagenergie een gekerfd proefstuk bij een bepaalde temperatuur kan opnemen. De vermelde temperatuur hoort altijd bij de waarde. Een resultaat bij kamertemperatuur is niet zonder meer bruikbaar voor een constructie die bij lage temperatuur werkt.</p>
<h2>Wat vertelt een datablad werkelijk?</h2>
<ul><li>Controleer of waarden typisch of gegarandeerd zijn.</li><li>Let op de warmtebehandeling en proefconditie.</li><li>Vergelijk de classificatie met de constructie-eis.</li><li>Houd rekening met verdunning door het basismateriaal.</li><li>Gebruik voor kritisch werk de goedgekeurde WPS en materiaalcertificaten.</li></ul>
""",
    },
    {
        "handle": "lasnormeringen-en-classificaties-uitgelegd",
        "title": "Lasnormeringen en classificaties praktisch uitgelegd",
        "summary": "Zo leest u EN ISO-, AWS- en materiaalclassificaties op lasdraad en elektroden zonder productcodes en goedkeuringen door elkaar te halen.",
        "image": "lasnormeringen-classificaties.png",
        "alt": "Cartoon van een lasser die classificaties van toevoegmaterialen vergelijkt",
        "tags": ["Lasnormen", "EN ISO", "AWS", "Classificatie", "Goedkeuring"],
        "body": """
<h2>Een classificatie is geen bestelnummer</h2>
<p>Op een verpakking lastoevoegmateriaal staan vaak een merknaam, artikelnummer, EN ISO-classificatie, AWS-classificatie, materiaalnummer en goedkeuringen naast elkaar. Deze gegevens hebben ieder een andere functie. Het artikelnummer identificeert het product; de classificatie beschrijft eigenschappen en toepassingsgebied volgens een norm.</p>
<h2>EN ISO en AWS</h2>
<p>EN ISO-classificaties worden veel gebruikt in Europa. AWS-classificaties komen uit het Amerikaanse systeem en worden internationaal eveneens vaak vermeld. De systemen zijn niet altijd één op één uitwisselbaar. Controleer daarom de volledige aanduiding en niet alleen een herkenbaar deel ervan.</p>
<h2>Hoe leest u een lange classificatie?</h2>
<p>Afhankelijk van het lasproces kan de code informatie bevatten over legeringstype, mechanische eigenschappen, kerftaaiheid, bekleding of vulling, beschermgas, laspositie en stroomsoort. De betekenis van iedere positie in de code wordt bepaald door de betreffende norm.</p>
<h3>Voorbeeld: EN ISO 14341-A: G 42 4 M21 3Si1</h3>
<p>Dit is een voorbeeld van een langere classificatie voor massieve draad voor het MAG-lassen van ongelegeerd en fijnkorrelig staal. U leest de aanduiding in vaste delen:</p>
<ul><li><strong>EN ISO 14341-A</strong>: de toegepaste productnorm en het classificatiesysteem.</li><li><strong>G</strong>: massieve draad/staaf voor gasbeschermd booglassen.</li><li><strong>42</strong>: de sterkteklasse van het neergesmolten lasmetaal.</li><li><strong>4</strong>: de kerftaaiheidsklasse; de norm koppelt dit teken aan een beproevingstemperatuur.</li><li><strong>M21</strong>: het gebruikte menggas volgens de bijbehorende gasindeling.</li><li><strong>3Si1</strong>: de chemische samenstellingsgroep van de lasdraad.</li></ul>
<p>Een korte aanduiding als <strong>ER70S-6</strong> volgens AWS A5.18 kan voor een vergelijkbare productgroep worden gebruikt, maar is geen letterlijke vertaling van de EN ISO-code. Vergelijk daarom altijd de volledige classificatie, mechanische waarden, het beschermgas en het productdatablad.</p>
<h3>Nog enkele voorbeelden</h3>
<ul><li><strong>EN ISO 14343-A: G 19 12 3 L Si</strong>: een voorbeeld voor een massieve RVS-lasdraad van het type 316L met verhoogd siliciumgehalte.</li><li><strong>AWS A5.9: ER316LSi</strong>: een veelgebruikte AWS-aanduiding voor dezelfde algemene RVS-productgroep.</li><li><strong>EN ISO 3581-A: E 19 12 3 L R 3 2</strong>: een voorbeeld van een langere classificatie voor een beklede RVS-elektrode. De laatste tekens beschrijven onder meer bekleding, laspositie en stroomcondities volgens de norm.</li></ul>
<h2>Materiaalnummer en handelsnaam</h2>
<p>Een Werkstoffnummer, zoals 1.4462, verwijst naar een materiaalsoort. Een handelsnaam is een commerciële productnaam. Twee producten met vergelijkbare classificatie kunnen verschillen in verwerking, booggedrag, goedkeuringen en verpakking.</p>
<h3>Voorbeelden van materiaalnummers</h3>
<ul><li><strong>1.4301</strong>: austenitisch RVS, vaak aangeduid als AISI 304.</li><li><strong>1.4307</strong>: koolstofarme variant, vaak aangeduid als AISI 304L.</li><li><strong>1.4404</strong>: molybdeenhoudend, koolstofarm RVS, vaak aangeduid als AISI 316L.</li><li><strong>1.4571</strong>: titaangestabiliseerd molybdeenhoudend RVS, vaak aangeduid als AISI 316Ti.</li><li><strong>1.4462</strong>: duplex RVS, vaak aangeduid als 2205.</li><li><strong>1.4828</strong>: hittebestendig austenitisch RVS, vaak aangeduid als AISI 309.</li></ul>
<p>De AISI-benaming is een veelgebruikte vergelijking en niet altijd een exacte één-op-één-equivalentie. Controleer bij materiaalkeuze daarom de voorgeschreven norm, samenstelling, levertoestand en het materiaalcertificaat.</p>
<h2>Classificatie versus goedkeuring</h2>
<p>Een normclassificatie beschrijft het toevoegmateriaal volgens vastgelegde criteria. Een goedkeuring van bijvoorbeeld een keuringsinstantie is een afzonderlijke product- of toepassingsbeoordeling. De aanwezigheid van een classificatie betekent dus niet automatisch dat iedere gewenste goedkeuring aanwezig is.</p>
<h2>Praktische controle bij productkeuze</h2>
<ul><li>Begin bij basismateriaal en constructie-eis.</li><li>Controleer lasproces, diameter, beschermgas en laspositie.</li><li>Lees de volledige classificatie.</li><li>Controleer vereiste mechanische eigenschappen en goedkeuringen.</li><li>Volg bij gekwalificeerd werk altijd de WPS.</li></ul>
""",
    },
]


def upload_file(client: ShopifyClient, path: Path, alt: str) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    staged = client.graphql(
        """mutation($input:[StagedUploadInput!]!){stagedUploadsCreate(input:$input){
          stagedTargets{url resourceUrl parameters{name value}} userErrors{field message}}}""",
        {"input": [{"resource": "FILE", "filename": path.name, "mimeType": mime, "httpMethod": "POST"}]},
    )["stagedUploadsCreate"]
    if staged["userErrors"]:
        raise RuntimeError(str(staged["userErrors"]))
    target = staged["stagedTargets"][0]
    fields = {item["name"]: item["value"] for item in target["parameters"]}
    response = requests.post(target["url"], data=fields, files={"file": (path.name, path.read_bytes(), mime)}, timeout=180)
    response.raise_for_status()
    created = client.graphql(
        """mutation($files:[FileCreateInput!]!){fileCreate(files:$files){
          files{... on MediaImage{id fileStatus image{url}}} userErrors{field message}}}""",
        {"files": [{"originalSource": target["resourceUrl"], "contentType": "IMAGE", "alt": alt}]},
    )["fileCreate"]
    if created["userErrors"]:
        raise RuntimeError(str(created["userErrors"]))
    file = created["files"][0]
    for _ in range(30):
        if file.get("fileStatus") == "READY" and (file.get("image") or {}).get("url"):
            return file["image"]["url"]
        time.sleep(1)
        file = client.graphql(
            """query($id:ID!){node(id:$id){... on MediaImage{id fileStatus image{url}}}}""",
            {"id": file["id"]},
        )["node"]
    raise RuntimeError(f"Afbeelding niet gereed: {path.name}")


def blog_id(client: ShopifyClient) -> str:
    nodes = client.graphql("query{blogs(first:20){nodes{id handle title}}}")["blogs"]["nodes"]
    match = next((node for node in nodes if node["handle"] == BLOG_HANDLE), None)
    if not match:
        raise RuntimeError(f"Shopify-blog /blogs/{BLOG_HANDLE} niet gevonden.")
    return match["id"]


def linked_test_description() -> str:
    product = get_supplier_product("certilas", "32912AP") or {}
    soup = BeautifulSoup(product.get("html_description") or "", "html.parser")
    links = {
        "Natteigenschappen": "/blogs/news/natteigenschappen-en-mechanische-eigenschappen-lasmetaal",
        "Putcorrosie en PREN": "/blogs/news/putcorrosie-en-pren-bij-rvs",
        "Lasposities": "/blogs/news/lasposities-pa-pg-uitgelegd",
        "Normeringen": "/blogs/news/lasnormeringen-en-classificaties-uitgelegd",
    }
    block = soup.new_tag("div")
    block["class"] = "product-uitleg"
    heading = soup.new_tag("h3")
    heading.string = "Technische uitleg"
    block.append(heading)
    listing = soup.new_tag("ul")
    labels = {
        "Natteigenschappen": "Natteigenschappen: betere vloeiing en aansluiting op de lasflanken.",
        "Putcorrosie en PREN": "PREN ≥ 35: verhoogde weerstand tegen putcorrosie.",
        "Lasposities": "Lasposities: PA, PB, PC, PD, PE, PF en PG.",
        "Normeringen": "Classificaties: AWS A 5.22 en EN ISO 17633-A.",
    }
    for key, url in links.items():
        li = soup.new_tag("li")
        li.append(labels[key] + " (")
        anchor = soup.new_tag("a", href=url, target="_blank", rel="noopener noreferrer")
        anchor["aria-label"] = f"Uitleg over {key}"
        anchor.string = "Uitleg"
        li.append(anchor)
        li.append(")")
        listing.append(li)
    block.append(listing)
    soup.append(block)
    return str(soup)


def main(*, apply: bool) -> dict:
    client = ShopifyClient.from_settings()
    scopes = {item["handle"] for item in client.graphql(
        "query{currentAppInstallation{accessScopes{handle}}}"
    )["currentAppInstallation"]["accessScopes"]}
    missing = sorted({"read_content", "write_content"} - scopes)
    if missing:
        return {"ready": False, "missing_scopes": missing, "articles": len(BLOGS), "test_sku": "32912AP"}
    if not apply:
        return {"ready": True, "articles": len(BLOGS), "test_sku": "32912AP"}
    target_blog = blog_id(client)
    created = []
    for article in BLOGS:
        image_url = upload_file(client, ASSET_DIR / article["image"], article["alt"])
        payload = client.graphql(
            """mutation($article:ArticleCreateInput!){articleCreate(article:$article){
              article{id title handle isPublished} userErrors{code field message}}}""",
            {"article": {
                "blogId": target_blog, "title": article["title"], "handle": article["handle"],
                "author": {"name": "Admin"}, "body": article["body"],
                "summary": article["summary"], "tags": article["tags"], "isPublished": False,
                "image": {"url": image_url, "altText": article["alt"]},
            }},
        )["articleCreate"]
        if payload["userErrors"]:
            raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
        created.append(payload["article"])
    test_product = upload_test_product_family(
        "certilas", "32912AP", description_html=linked_test_description()
    )
    return {"ready": True, "created": created, "test_product": test_product}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(main(apply=args.apply), ensure_ascii=False))
