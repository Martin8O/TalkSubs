# TalkSubs: krok za krokem

Stejný návod je na stránce Nápověda v aplikaci. [English](guide.md) · [README](../README.md)

**Instalace**

1. Rozbalte TalkSubs do složky, kam můžete zapisovat a kterou nesynchronizuje OneDrive (třeba na jiném disku), ne do Program Files.
2. Poklepejte na TalkSubs.bat. Když Windows varuje před souborem z internetu, zvolte „Další informace“ a „Přesto spustit“.

- [První spuštění](#start)
- [Přidání přednášky](#add)
- [Poslech přednášky](#listen)
- [Uložení titulků](#save)
- [Úpravy titulků](#edit)
- [Přehrávání a pohyb](#player)
- [Překlad](#translate)
- [Výrazy a jména](#terms)
- [Vlastní druhy titulků](#types)
- [Nastavení a vzhled](#settings)
- [Odebrání přednášky](#remove)
- [Soukromí a zabezpečení](#privacy)

<a id="start"></a>
## První spuštění

1. Při prvním spuštění se jednou stáhne model řeči (2,5 GB); okno ukazuje průběh a zbývající čas. Když se stahování přeruší, stiskněte „Zkusit znovu“: pokračuje, kde skončilo.
2. Pak už aplikace pracuje bez internetu.
3. Na počítači s grafickou kartou NVIDIA okno nabídne jednou stáhnout její podporu (1,7 GB, oficiální soubory NVIDIA); převod pak běží na kartě, zhruba dvakrát rychleji (na počítači s pomalejším procesorem víc). Do té doby, a na každém jiném počítači, běží na procesoru.

<a id="add"></a>
## Přidání přednášky

1. Na stránce Fronta stiskněte „Vybrat soubory…“ a vyberte zvukové nebo video soubory. Zůstanou, kde jsou.
2. Nebo soubory přetáhněte do okna: aplikace si nechá vlastní zašifrovanou kopii jejich zvuku.
3. Nebo vložte odkaz na nahrávku a stiskněte „Přidat odkaz“: stáhne se a uloží jen zvuk, zašifrovaný.
4. Přednášky se přepisují jedna po druhé. Každá ukazuje průběh a zbývající čas; „Zrušit“ ji zastaví.
5. Přepis přerušený zavřením aplikace nebo pádem pokračuje při dalším spuštění tam, kde skončil.

<a id="listen"></a>
## Poslech přednášky

1. U přednášky ve frontě stiskněte „Ukázat náhled“ a poslechněte si ji přímo v okně.

<a id="save"></a>
## Uložení titulků

1. U hotové přednášky stiskněte „Změnit“ a vyberte druhy titulků, typy souborů a co uložit (originál v angličtině, překlad, nebo obojí).
2. Stiskněte „Uložit titulky“. Soubory se uloží vedle původního souboru, nebo do jedné složky, podle Nastavení (přetažené soubory a odkazy vždy do složky).
3. Když soubory se stejným jménem už existují, aplikace se zeptá, zda je „Přepsat“, nebo ponechat.
4. „Otevřít složku“ ukáže uložené soubory ve Windows. Nové uložení s jinou volbou nepotřebuje nový přepis.

<a id="edit"></a>
## Úpravy titulků

1. U hotové přednášky stiskněte „Upravit titulky“; nahoře vyberte, který druh titulků upravujete.
2. Vyberte titulek v seznamu nebo na průběhu zvuku a opravte nahoře jeho text (každý řádek textu je jeden řádek titulku).
3. Začátek nebo konec posunete tažením okraje na průběhu zvuku, tlačítky − a + (o jedno políčko), nebo napíšete čas a stisknete Enter.
4. „Rozdělit u kurzoru“, „Spojit s dalším“, „Nový titulek“ a „Smazat“ mění samotné titulky. Tažením celého titulku v průběhu zvuku ho posunete i s jeho délkou; ↑ a ↓ (v textovém poli Alt+↑ ↓) přejdou na předchozí nebo další titulek.
5. Červené značky ukazují, co je nutno opravit, žluté, co stojí za pohled; „Další problém“ přejde na další označený titulek.
6. Vše se ukládá samo; „Vrátit“ a „Opakovat“ (Ctrl+Z, Ctrl+Y) vracejí a znovu provádějí změny.
7. „Vytvořit znovu z nahrávky“ udělá titulky znovu podle dnešních pravidel (ne když mají překlad).

<a id="player"></a>
## Přehrávání a pohyb

1. „Přehrát“ spustí a pozastaví přehrávání (mezerník); „Zastavit“ zastaví a vrátí na začátek.
2. „« 10 s“ a „10 s »“ skočí o deset sekund zpět nebo vpřed (klávesy ← a →).
3. Velký čas vedle tlačítek ukazuje, kde nahrávka právě je.
4. Dvojklik na titulek v průběhu zvuku přehraje od jeho začátku; klik na prázdné místo tam přesune místo přehrávání.
5. − a + přibližují průběh zvuku. „Ovládání“ ukáže všechny klávesy.

<a id="translate"></a>
## Překlad

1. Když sloupec pro překlad nevidíte, zaškrtněte nahoře v editoru „Zobrazit překlad“ (volba se zapamatuje).
2. V editoru je sloupec seznamu s nápisem „Překlad“ pro překlad. Klikněte do něj, nebo pište do pole „Překlad“ pod anglickým textem.
3. Nad polem nastavte kód jazyka překladu (cs, sk, de …); podle něj se pojmenují přeložené soubory.
4. Nad polem je vidět nejdelší řádek a rychlost čtení překladu proti mezím druhu titulků.
5. Pro uložení překladu zaškrtněte při ukládání v řádku „Uložit“ „Překlad“. Soubory se jmenují třeba „prednaska.cz-template.cs.srt“; zatím nepřeložené titulky v nich chybějí. Tabulka Excel má překlad vždy ve sloupci Translation.

<a id="terms"></a>
## Výrazy a jména

1. V Nastavení je v části „Výrazy a jména“ váš seznam jmen a výrazů, které model řeči píše špatně.
2. Přidávejte je jeden na řádek: správný tvar, pak = a čárkami oddělené tvary, které píše model řeči (třeba: Nietzsche = Neechee).
3. Stiskněte „Uložit nastavení“. Titulky, které jste neupravovali, se řídí novým seznamem.

<a id="types"></a>
## Vlastní druhy titulků

1. V Nastavení najdete v části „Druhy titulků“ druhy, které má aplikace, i vaše vlastní.
2. Vlastní druh uděláte podle zveřejněných zásad („Nový druh podle zásad“) nebo jako kopii druhu, který má aplikace.
3. Tlačítkem „Upravit“ nastavíte hodnoty: délku řádku, počet řádků, rychlost čtení, časování, slova a přísnost pravidel.
4. Stiskněte „Uložit nastavení“. Zaškrtněte druh v „Předem zaškrtnuté při ukládání“, aby se jeho titulky dělaly u každé nové přednášky.
5. „Odebrat“ druh odloží: titulky s ním udělané jdou dál otevřít i uložit a „Vrátit“ ho vrátí; „Smazat natrvalo“ odebraný druh smaže.

<a id="settings"></a>
## Nastavení a vzhled

1. V Nastavení vyberete, které druhy titulků a typy souborů jsou předem zaškrtnuté a kam se titulky ukládají.
2. „Tento počítač“ ukazuje, zda aplikace používá grafickou kartu, nebo procesor; „Změřit znovu“ přeměří odhad času.
3. Vpravo nahoře přepnete mezi světlým a tmavým vzhledem a mezi češtinou a angličtinou.

<a id="remove"></a>
## Odebrání přednášky

1. U hotové přednášky stiskněte „Odebrat“. Aplikace řekne, co se smaže a co zůstane, a nejdřív se zeptá.
2. Smaže přepis, vaše úpravy a své kopie zvuku; váš původní soubor a uložené titulky zůstanou.

<a id="privacy"></a>
## Soukromí a zabezpečení

1. Všechno se zpracovává na tomto počítači; nic se nikam neposílá.
2. Co si aplikace ukládá, je zašifrované klíčem zamčeným k vašemu účtu Windows. Když aplikace svou uloženou práci neotevře (jiný účet Windows nebo počítač, chybějící klíč), „Začít znovu“ začne s prázdnou aplikací a starou práci odloží stranou, nic nesmaže.
3. Podrobnosti jsou v části „Zabezpečení a soukromí“ na stránce Nápověda v aplikaci a v README.
