# Laboratorium 2 import i agregacja BDOT10K

Skrypt `app/bdot.py` pobiera oficjalną paczkę GUGiK dla powiatu
sochaczewskiego (TERYT 1428), importuje wszystkie warstwy przestrzenne
do schematu `bdot` i oblicza agregaty dla trzech rozdzielczości H3: 7, 8 i 9.
Przeznaczony jest wyłącznie dla środowiska `env-test`.

## Układy współrzędnych

Wszystkie naprawy geometrii, przycinanie, przecięcia, pomiary powierzchni
i długości oraz przypisanie punktów wykonywane są w EPSG:2180.
Wyłącznie interfejs indeksowania komórek H3 wymaga EPSG:4326. Współrzędne
komórek są od razu przeliczane do EPSG:2180 przed analizą.
Po zakończeniu obliczeń geometrie wyników są przeliczane do EPSG:3857;
wartości kolumn nie są ponownie mierzone w tym układzie.

Schematy w bazie testowej:

- `bdot` — dane BDOT10K, zachowane atrybuty, geometrie w EPSG:2180,
  naprawione w razie potrzeby i przycięte do granicy powiatu.
- `analiza` — granica powiatu i siatki robocze `h3_r7`, `h3_r8`, `h3_r9`
  w EPSG:2180 wraz z obliczonymi wartościami.
- `wyniki` — `h3_r7`, `h3_r8`, `h3_r9` i granica powiatu w EPSG:3857;
  tabele `warstwy`, `kontrola` i `zrodlo` opisują obliczenia oraz dane wejściowe.

Pierwotne tabele `grid.h3_r7`, `grid.h3_r8`, `grid.h3_r9` z poprzedniej
części ćwiczenia pozostają bez zmian. Do analizy dodano komórki przecinające
granicę powiatu i przycięto je w EPSG:2180, aby nie pomijać obiektów na obrzeżu.
Dlatego liczba komórek analitycznych jest większa niż w siatce wybieranej
wcześniej według położenia środków.

## Znaczenie kolumn

Każda warstwa BDOT10K ma własną kolumnę:

- `ot_..._a_m2` — suma powierzchni fragmentów poligonów wewnątrz komórki, w m².
- `ot_..._l_m` — suma długości fragmentów linii wewnątrz komórki, w metrach.
- `ot_..._p_n` — liczba punktów w komórce.

Rodzaj miary wynika z geometrii warstwy. Metadane zawierają dokładne
przyporządkowanie warstwa–kolumna–jednostka. Każda tabela wynikowa zawiera
także `h3_id`, `resolution`, `cell_area_m2` (powierzchnia przyciętej komórki
obliczona w EPSG:2180) i `geom`.

Dane BDOT10K są przycięte do powiatu przed pomiarem. Przecięcia do pomiarów
wykorzystują pełne komórki robocze (`analiza.h3_r*.full_geom`), aby nie ciąć
drugi raz tej samej granicy powiatu i nie tracić odcinków wskutek precyzji
zmiennoprzecinkowej. Do mapy wynikowej trafia wyłącznie przycięta `geom`.

Punkty na wspólnej granicy komórek są przypisywane raz, do najmniejszego
`h3_id`. Dla linii wspólne odcinki graniczne również są liczone tylko raz.
Linie mierzone są odcinkami; zachowana jest długość również wtedy, gdy
pojedyncza linia wraca po swojej trasie.
Poligony przecinające kilka komórek są dzielone i mierzone w każdej z nich.
Powierzchnie nakładających się obiektów w jednej warstwie są sumowane jako
powierzchnie obiektów, bez ich scalania. Brak obiektów w komórce daje zero.

## Uruchomienie

Na komputerze z działającym Dockerem i istniejącą bazą `env-test` uruchom
w PyCharmie plik `run_bdot.py`. Odczytuje on konfigurację istniejącego
kontenera bez wypisywania hasła i wykonuje cały proces w kontenerze Pythona.

Na runnerze import i analiza wykonywane są automatycznie przez
`start_services.yml`, wyłącznie dla brancha `env-test`, po załadowaniu granicy
powiatu. Pobrana paczka jest przechowywana w wolumenie `bdot_cache`.
Opcja `python /app/bdot.py --refresh` wymusza pobranie nowej wersji paczki.

Skrypt najpierw oblicza wyniki w osobnych schematach roboczych. Dopiero gdy
wszystkie kontrole przejdą, zastępuje schematy `bdot`, `analiza`, `wyniki`
w jednej transakcji. Błąd obliczeń pozostawia poprzedni opublikowany wynik.
Równoległe wykonanie blokuje blokada doradcza PostgreSQL.

## Kontrola wyników

`wyniki.kontrola` porównuje dla każdej warstwy i rozdzielczości sumę
w komórkach z sumą w powiecie. Liczby punktów muszą być identyczne.
Dla miar zmiennoprzecinkowych tolerancja wynosi większą z wartości:
0,01 jednostki lub 0,000001% sumy źródłowej.
Siatki muszą pokrywać cały powiat z różnicą powierzchni poniżej 1 m².

```sql
SELECT resolution, count(*) AS liczba_kontroli, bool_and(poprawne) AS wszystkie_poprawne
FROM wyniki.kontrola GROUP BY resolution ORDER BY resolution;

SELECT warstwa, wymiar, kolumna, jednostka, liczba_w_powiecie
FROM wyniki.warstwy ORDER BY warstwa;
```

W QGIS dodaj tabele `wyniki.h3_r7`, `wyniki.h3_r8`, `wyniki.h3_r9`
oraz `wyniki.powiat`. Wybierz dowolną kolumnę miary do stylu stopniowanego.

## Wynik dla pobranej paczki

Zaimportowano 66 warstw i 149 479 obiektów po przycięciu do granicy powiatu.
Siatki wynikowe zawierają 199 komórek dla r7, 1205 dla r8 i 7925 dla r9.
Każda tabela ma 66 kolumn miar BDOT10K. Wszystkie 198 bilansów sum przeszło
kontrolę. Zweryfikowano poprawność geometrii, układy 2180 i 3857 oraz
zachowanie wartości liczbowych po transformacji.

SHA256 paczki: `842939e25f41e6dd4e567b76ea9c4ae9abad72910e5d7f72a1f1d44c09e8ed47`.
Liczby mogą zmienić się po pobraniu nowszej wersji danych przez `--refresh`.

Źródło danych i opis pobierania:
https://www.geoportal.gov.pl/pl/dane/baza-danych-obiektow-topograficznych-bdot10k/

Paczka powiatu:
https://opendata.geoportal.gov.pl/bdot10k/schemat2021/GPKG/14/1428_GPKG.zip
