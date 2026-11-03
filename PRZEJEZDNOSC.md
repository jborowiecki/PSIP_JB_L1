# Analiza wielokryterialna przejezdności terenu

Model laboratoryjny dla wojskowego pojazdu kołowego, powiat sochaczewski
(TERYT 1428), H3 r7, r8 i r9. Nie jest to model prędkości, prawdopodobieństwa
przejazdu ani potwierdzenie dostępności konkretnej trasy.

## Uruchomienie w PyCharmie

Uruchom `run_przejezdnosc.py` lub konfigurację **Przejezdność — analiza env-test**.
Potrzebny jest Docker oraz istniejąca baza `env-test` z zaimportowanym BDOT10K
(`run_bdot.py`). Launcher buduje obraz, uruchamia testy, obliczenia i eksport.
Wszystkie biblioteki analityczne działają w kontenerze; lokalny Python
korzysta z biblioteki standardowej. Hasło pobierane jest z istniejącego
kontenera i przekazywane przez środowisko, bez wypisywania.

Wyniki: `qgis/przejezdnosc/`. Przy zainstalowanym QGIS 3.36 launcher tworzy
projekt `przejezdnosc_3857.qgz` i podgląd `podglad_przejezdnosc_r8.png`.
W QGIS otwórz projekt; siatki r7, r8 i r9 oraz tabele wag są dostępne w panelu.
Domyślnie widoczna jest r8. Projekt ma względne ścieżki — przenoś cały katalog.

### Bezpośredni dostęp do bazy Docker

Główny projekt `przejezdnosc_postgis.qgz` (także pod dotychczasową nazwą
`przejezdnosc_3857.qgz`) korzysta teraz bezpośrednio z PostGIS, zamiast z kopii
w GeoPackage i CSV. Wymaga działającego kontenera `remote-db-env-test-postgis-1`:
host `localhost`, port `5434`, baza i użytkownik `psip`.

Połączenie w panelu **Przeglądarka → PostgreSQL → PSIP_env_test_Docker**
udostępnia schematy `bdot`, `analiza`, `wyniki`, `grid`, `przejezdnosc_robocza`,
`przejezdnosc` oraz źródłową granicę `public.powiat`. Projekt grupuje 91 tabel
aplikacyjnych w 94 warstwy/tabele QGIS: trzy tabele `analiza.h3_r*` mają po
dwie kolumny geometrii (`geom` oraz `full_geom`), pokazywane osobno.
Uwzględniono wszystkie 66 warstw BDOT10K oraz tabele kontroli, maksimów,
wag, wrażliwości i źródeł. Początkowo widoczne są tylko r8 i granica powiatu;
pozostałe warstwy można włączać w grupach. Zachowany OpenStreetMap jest
oddzielnym podkładem internetowym, a nie częścią bazy PostGIS.

Style trzech siatek przejezdności i granicy zapisano również w bazie
(`public.layer_styles`), aby można było odtworzyć kolory po ponownym dodaniu
tabel. Warstwy w projekcie ustawiono do odczytu; nie zmieniono uprawnień
użytkownika bazy ani danych wejściowych. Hasło znajduje się wyłącznie
w lokalnym pliku uwierzytelniania libpq użytkownika, poza projektem i paczką ZIP.

Po ponownym wykonaniu analizy odśwież mapę (F5). Sama zmiana wag w pliku JSON
nie aktualizuje obliczeń — trzeba uruchomić `run_przejezdnosc.py`.
Ten launcher po obliczeniach ponownie przygotowuje projekt PostGIS.
Samo połączenie, bez ponownego liczenia, można odtworzyć przez
`configure_qgis_postgis.py` lub konfigurację **QGIS — połączenie PostGIS Docker**.
Opcja `--output-dir KATALOG` pozwala wskazać folder z istniejącym projektem.

Kopia plikowa do pracy bez działającego Dockera jest zachowana jako
`przejezdnosc_offline_3857.qgz`; korzysta z lokalnych plików GPKG i CSV.
Zabierając wyniki na inny komputer, korzystaj z tej kopii i przenieś cały
katalog. Połączenie PostGIS wskazuje lokalną bazę na komputerze Jana.
Kontrole odczytu tabel i źródeł są zapisane w `kontrola_polaczenia_postgis.json`.

Opcje launchera: `--no-build`, `--no-qgis`, `--output-dir KATALOG`.
Po zmianie kodu lub wag trzeba przebudować obraz (nie używaj `--no-build`).

## Dane, miary i CRS

Program odczytuje surowe agregaty z `analiza.h3_r7`, `h3_r8`, `h3_r9`.
Wszystkie przecięcia i pomiary poprzedniego etapu zostały wykonane w EPSG:2180.
Nie wykonujemy ponownych pomiarów w EPSG:3857.

- poligony: suma powierzchni fragmentów obiektów w m²;
- linie: suma długości fragmentów w metrach;
- punkty: liczba punktów przypisanych do heksa.

Wszystkie występujące warstwy BDOT10K są objęte jawną konfiguracją wag
`app/config/przejezdnosc_wagi.json`: 66 wag, w tym 25 niezerowych dla obecnej
paczki. Nowa warstwa bez skonfigurowanej wagi przerywa analizę — nie otrzymuje
domyślnej wagi. Są to warstwy klas obiektów, nie wszystkie podtypy atrybutowe.
PTLZ oznacza tutaj całą klasę terenów leśnych i zadrzewionych: lasy, zagajniki
i zadrzewienia. Nie jest to wyłącznie filtr `rodzaj=las`.

W źródłowej paczce warstwa BUIB_L jest odczytywana jako `ot_biub_l`;
zachowujemy rzeczywistą nazwę, a w metadanych opisujemy ją jako inne budowle.

## Wzór i mianownik

Dla kryterium j i rozdzielczości r:

`M[j,r] = MAX(x[h,j,r])` po wszystkich heksach powiatu w danej siatce.

`z[h,j,r] = x[h,j,r] / M[j,r]`, jeśli maksimum jest większe od zera.

`IPT[h,r] = SUM(w[j] * z[h,j,r]) / N[r]`.

Normalizacja należy do [0,1], wagi do [-1,1]. `N[r]` to liczba warstw
o dodatnim maksimum, **również z wagą zero**. Przy aktualnych danych N=66
w każdej rozdzielczości. Kryterium o maksimum 0 jest wyłączone z mianownika;
jego znormalizowana wartość i wkład wynoszą 0. Brak całej warstwy,
NULL, wartość ujemna lub nieskończona są błędem, nie zerem.

Normalizacja jest osobna dla r7, r8 i r9. Wyniki oznaczają pozycję względem
heksów tego samego powiatu i rozdzielczości; nie są bezwzględną skalą
umożliwiającą porównywanie różnych powiatów lub rozdzielczości.

Mierzymy sumy, a nie gęstości: heksy brzegowe przycięte do powiatu mają
mniejszą powierzchnię, co może obniżać ich miary. Nie wprowadzamy potajemnie
dzielenia przez powierzchnię, bo zmieniałoby to uzgodnioną metodę.

## Wagi i ich ograniczenia

Wagi zapisują autorskie założenia laboratoryjne uzgodnione przed analizą,
a nie współczynniki wyznaczone pomiarami lub przez cytowane publikacje.
Drogi +1; place +0,4; roślinność trawiasta i uprawy rolne +0,2;
budynki -0,6; tereny leśne lub zadrzewione -0,8; mokradła i wody -1.
Pełna lista i uzasadnienia znajdują się w konfiguracji i tabeli wynikowej.

Wagi zerowe oznaczają brak uzasadnionego dodatkowego wpływu na podstawie
dostępnej miary, powtarzanie informacji lub potrzebę podziału klasy mieszanej.
Np. jezdnie są wyzerowane ze względu na drogi; administracja i status ochronny
nie są fizycznymi przeszkodami. Niektóre klasy nadal częściowo się nakładają:
model nie gwarantuje statystycznej niezależności kryteriów.

Nie znamy konkretnego pojazdu, nośności i wilgotności gruntu, nachylenia,
rozstawu drzew i budynków, nośności mostów ani ciągłości i dostępności dróg.
Heks z drogą i wodą nie musi posiadać przejezdnej przeprawy. Dodatni wynik
nie jest potwierdzeniem możliwości przejazdu, a ujemny nie stanowi dowodu
nieprzejezdności. Znak służy wyłącznie porównaniu przyjętych czynników.

Podstawy kierunku wpływu (nie dokładnych wag):
- ATP 4-11, Army Motor Transport Operations, załącznik D:
  https://rdl.train.army.mil/catalog-ws/view/100.ATSC/8D6DF8FA-FCD5-4A6F-BEE4-B15D10BE1BF5-1373892396033/atp4_11.pdf
- ERDC, badania mobilności i podłoża:
  https://www.erdc.usace.army.mil/Media/Publication-Notices/Tag/5209/mobility/
- Vademecum użytkownika BDOT10K, klasyfikacja:
  https://wodgik.lubuskie.pl/uploads/VADEMECUM_UZYTKOWNIKA_BDOT10k_2024.pdf

## Wynik, pola i kolorystyka

Schemat `przejezdnosc_robocza`: trzy siatki z geometrią EPSG:2180.
Schemat `przejezdnosc`: trzy siatki wynikowe i powiat z geometrią EPSG:3857.
Wartości liczbowe są kopiowane bez przeliczania po transformacji.
Tabele wag, maksimów i wrażliwości są nieprzestrzenne.

- oryginalne kolumny `ot_...`: miary przed normalizacją;
- `n_ot_...`: wartości znormalizowane [0,1];
- `wk_ot_...`: `waga * wartość znormalizowana`, przed podziałem przez N;
- `ipt`: końcowy indeks;
- `liczba_kryteriow`: mianownik N;
- `liczba_wag_niezerowych`: liczba aktywnie wpływających warstw;
- `ipt_min_wrazliwosc`, `ipt_max_wrazliwosc`: możliwy przedział przy
  jednoczesnej zmianie wielkości wszystkich niezerowych wag o ±20%;
- `znak_stabilny`: czy w całym tym przedziale wynik ma ten sam znak.

Mapa ma stały zakres i ciągłą, **nieliniową skalę barw**: -1 czerwony
(255,0,0), 0 biały (255,255,255), +1 zielony (0,255,0).
Wiele wag zerowych oraz dzielenie przez N skupiają wyniki blisko zera;
wcześniejsza liniowa skala pokazywała tylko 5–6 bardzo podobnych odcieni.
Żeby rozróżnić heksy bez zmieniania obliczeń, stosujemy logarytm ze znakiem:

`t = sign(IPT) * ln(1 + abs(IPT)/0.0001) / ln(10001)`

`p = (1+t)/2`, kolor
`RGB = (round(255*min(1,2*(1-p))), round(255*min(1,2*p)), round(510*min(p,1-p)))`.

Parametr 0,0001 jest stałym ustawieniem wizualizacji, nie progiem fizycznej
przejezdności. Funkcja jest ciągła, symetryczna względem zera i ściśle rosnąca
przed zaokrągleniem do kanałów RGB. Rozróżnia niewielkie wartości w pobliżu
zera, zachowując krańce -1 i +1. Wszystkie trzy siatki mają identyczne
odwzorowanie: ten sam IPT zawsze daje ten sam kolor. Nie używamy lokalnego
min/max ani kwantyli. Legenda podaje rzeczywiste wartości IPT na nieliniowej osi.

Nie zmieniamy normalizacji kryteriów, wag, mianownika N, wartości IPT ani
rankingu heksów. Różnice kolorów nie oznaczają proporcjonalnych różnic IPT.
Nieliniowe barwy poprawiają czytelność, ale nie stanowią dodatkowej walidacji
modelu ani dowodu większej przejezdności. Ustawienia i suma kontrolna danych
są zapisane w `styl_mapy.json` oraz metadanych warstw QGIS.

## Wrażliwość i kontrola

Każda niezerowa waga jest osobno mnożona przez 0,8 oraz 1,2,
z ograniczeniem do [-1,1]. Waga +1 pomnożona przez 1,2 pozostaje +1:
raport jawnie wskazuje ograniczenie i brak rzeczywistej zmiany.
Znaki wag są zachowane, zera pozostają zerami, maksima i mianownik są stałe.

Dla każdego scenariusza obliczamy średnią i maksymalną zmianę IPT,
liczbę zmian znaku oraz korelację rang Spearmana ze średnimi rangami przy
remisach. Przy stałym rankingu wyników bez wariancji korelacja jest NULL,
ponieważ jest matematycznie nieokreślona. Przedział zmian wszystkich wag
jest dokładną obwiednią tego liniowego modelu, a nie przedziałem ufności.

Testy obejmują ręcznie policzone przykłady, maksima osobno dla rozdzielczości,
wagi zerowe, zerowe maksima, granice wag i błędne wejścia. Na rzeczywistych
danych niezależne zapytania SQL ponownie obliczają normalizację i IPT ze
źródłowych miar oraz sprawdzają zakresy, geometrię i zachowanie wartości
liczbowych podczas transformacji. Najpierw sprawdzane są też wszystkie
kontrole poprzedniego etapu BDOT10K.

Publikacja wyników odbywa się w jednej transakcji; błąd przywraca poprzedni
wynik. Wspólna blokada doradcza zapobiega równoległemu importowi BDOT i analizie.
Workflow uruchamia analizę tylko dla `env-test`, po pomyślnym imporcie BDOT.
