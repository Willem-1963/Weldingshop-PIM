# Facebook-blogposts

De sectie Facebook-blogposts (?section=facebook) deelt maximaal één gepubliceerd
Shopify News-artikel per Nederlandse kalenderdag op pagina 1107768209082479.
Standaardtijd: 10:00 Europe/Amsterdam. De systemd-timer controleert iedere 15 minuten.
Planning staat standaard UIT, ook na het opslaan of vernieuwen van een verbinding.

## Verbinding

Voer uitsluitend een nieuw paginatoegangstoken in via de PIM, niet via chat.
De app vereist pages_show_list, pages_read_engagement en pages_manage_posts.
De server controleert /me op het vaste pagina-ID en leest de volledige
berichtenlijst voordat hij het token versleuteld opslaat.
Versleuteling gebruikt dezelfde Fernet-sleutel als de bestaande PIM-koppelingen.
De database is data/database/facebook_blog.sqlite en bevat ook de permanente historie.
Gebruik voor langdurige werking een passend langlevend paginatoken.
Een verlopen of ongeldig token schakelt de planning uit; koppel opnieuw via Verbinding.

## Werking

Alle gepubliceerde News-artikelen worden met paginering uit Shopify gelezen.
Toekomstige publicaties en andere blogs worden uitgesloten.
Facebook /posts wordt volledig gepagineerd, met links uit message en geneste attachments.
Trackingparameters en Facebook-uitgaande redirects worden genormaliseerd.
Een exacte lange artikeltitel in berichttekst telt conservatief ook als al gedeeld.
Er wordt geen Facebook paging.next-URL gevolgd of opgeslagen: deze kan een token bevatten.

Het nieuwste ontbrekende artikel wordt geselecteerd. Oudere artikelen kunnen
in Planning worden uitgeschakeld; dan komen alleen artikelen van die lokale dag in aanmerking.
Een voorbeeldcontrole leest en registreert bestaande berichten, maar plaatst niets.
Een feed-linkpost gebruikt de titel, korte introductie en de artikel-URL.
Facebook maakt de linkvoorvertoning uit de metadata van het artikel; een afbeelding
wordt niet als apart fotobericht geüpload.

De volledige controle staat onder een exclusieve proceslock. Een dagelijkse reservering
en artikel-ID worden vastgelegd voordat de POST wordt uitgevoerd.
Bij een timeout of onduidelijke reactie wordt de uitkomst onzeker en de planning uitgezet.
Er volgt geen blinde retry. Een volgende voorbeeldcontrole kan een inmiddels gevonden
Facebook-link herkennen en de registratie herstellen. Zonder bevestiging blijft het
artikel gereserveerd. Laat een beheerder de echte Facebook-uitkomst onderzoeken
voordat een onzekere registratie handmatig wordt vrijgegeven.

## Historie en beheer

Historie bevat datum, status, titel, toelichting, artikellink, Facebook-berichtlink
en bericht-ID. De laatste 300 gebeurtenissen zijn doorzoekbaar en exporteerbaar.
Alle gebeurtenissen blijven in de database bewaard.
Geen tokens of onbewerkte HTTP/API-fouten worden in deze historie opgeslagen.
Vóór het inschakelen: gebruik Controleer en toon volgend bericht.

Service: weldingshop-facebook-blog.service.
Timer: weldingshop-facebook-blog.timer.
Een uitgeschakelde planning maakt geen netwerkverzoeken.

## Controle

python -m unittest tests.test_facebook_blog tests.test_facebook_page -v

De tests gebruiken een tijdelijke database en nep-Meta-clients. Geen echte plaatsingen.
