# Vaste werkwijze op iedere computer

Gebruik voor PIM en ERP ieder een eigen lokale Git-clone. Werk nooit rechtstreeks
in een release- of productiemap en kopieer geen losse bronbestanden tussen computers.

## Sessie starten

1. Open een terminal in de repository.
2. Voer `scripts/work-session-start.sh` uit.
3. Start Codex pas wanneer het script meldt dat de werkmap actueel is.
4. Laat Codex alle wijzigingen, tests en documentatie in deze repository maken.

Het startscript stopt bewust wanneer de vorige sessie onafgeronde lokale wijzigingen
heeft. Los die eerst op; overschrijf ze niet met `reset`, `checkout` of losse kopieën.

## Sessie afsluiten

Voer uit:

```bash
scripts/work-session-end.sh "korte duidelijke beschrijving"
```

Dit draait de tests, commit de wijzigingen, verwerkt nieuwe centrale commits met
rebase en pusht alles naar `origin/main`. Sluit Codex of de computer pas nadat het
script de gedeelde commit-hash toont.

## Productie

- PIM en ERP worden alleen vanuit een geteste Git-commit gedeployed.
- `.env`, sleutels, databases, imports, logs, uitvoer en back-ups horen nooit in Git.
- Een productie-hotfix moet dezelfde dag ook in de bronrepository worden gecommit.
- Begin op een andere computer altijd opnieuw met het startscript.
