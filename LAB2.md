# Laboratorium 2 zadanie 2 punkty 1–3

Rozwiązanie generuje siatki heksagonów dla powiatu sochaczewskiego
i ładuje je do trzech niezależnych baz PostGIS.

`app/python/h3_generator.py` generuje siatki dla `data/powiat.shp` w rozdzielczościach
7, 8 i 9. Większa rozdzielczość oznacza mniejsze komórki. Skrypt obsługuje
poligony, multipoligony i otwory oraz przelicza dane do EPSG:4326.
Wybiera pełne komórki, których środki leżą w powiecie, zgodnie z `h3.geo_to_cells`.
Komórki nie są przycinane; przy granicy mogą wychodzić poza powiat i pozostawiać
niewielkie fragmenty powiatu poza siatką.

`app/load.py` importuje powiat do `public.powiat` oraz siatki do `grid.h3_r7`,
`grid.h3_r8`, `grid.h3_r9`. Każda tabela siatki zawiera `h3_id`, `resolution`,
`geom` (Polygon, EPSG:4326), klucz główny i indeks GiST.
Powtórny import zastępuje zawartość wybranych tabel bez duplikatów.
Siatki są aktualizowane w jednej transakcji. Import powiatu jest osobną operacją
ogr2ogr i zastępuje tabelę `public.powiat`.

## Uruchomienie lokalne

Uruchom Docker Desktop z kontenerami Linux.

W PyCharmie można wybrać konfigurację „Laboratorium 2” albo uruchomić
plik `run_lab2.py` przez zwykły interpreter Python 3.12. Uruchomienie tego
pliku buduje kontenery i wykonuje loader; biblioteki GIS są instalowane
w kontenerze, a nie w lokalnym interpreterze PyCharma.
Alternatywnie w folderze repozytorium wykonaj:

```powershell
docker compose -f docker-compose-local.yml up -d --build --wait
docker compose -f docker-compose-local.yml exec python python /app/load.py
```

Sam generator można uruchomić poleceniem:

```powershell
docker compose -f docker-compose-local.yml exec python python /app/python/h3_generator.py --resolutions 7 8 9 --output-dir /tmp/h3
```

GeoJSON z samodzielnego generatora pozostaje w kontenerze; `load.py` zapisuje
wyniki bezpośrednio do bazy. Stara ścieżka `app/h3_generator.py` nadal działa.

## Środowiska env-test env-qa main

Workflow `start_services.yml` po pushu na każdy z trzech branchy uruchamia
PostGIS, czeka na gotowość i wykonuje loader w sieci tego projektu Compose.
Kod i dane są kopiowane do obrazu Pythona podczas budowania. Dzięki temu
runner w kontenerze nie wymaga współdzielenia ścieżek z hostem Dockera.
Można też uruchomić go ręcznie na wybranym branchu.
Zmiany trzeba umieścić na wszystkich trzech branchach, aby zasilić wszystkie bazy.
Każde środowisko GitHub musi mieć sekrety `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_DB`, `POSTGRES_PORT`. Porty hosta muszą być różne dla trzech baz.
Projekty i wolumeny `remote-db-<branch>` pozostają rozdzielone.

`H3_RESOLUTIONS` pozwala zmienić rozdzielczości, np. `7,8,9`.
Połączenie loadera korzysta z `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`,
`PGPASSWORD`. Hasła środowiskowe nie są zapisane w kodzie.

## Kontrola

W QGIS połącz się z właściwą bazą i dodaj trzy tabele ze schematu `grid`.
W każdej bazie wykonaj:

```sql
SELECT 'h3_r7' AS warstwa, count(*) AS liczba,
       bool_and(ST_IsValid(geom)) AS poprawne, min(ST_SRID(geom)) AS srid FROM grid.h3_r7
UNION ALL
SELECT 'h3_r8', count(*), bool_and(ST_IsValid(geom)), min(ST_SRID(geom)) FROM grid.h3_r8
UNION ALL
SELECT 'h3_r9', count(*), bool_and(ST_IsValid(geom)), min(ST_SRID(geom)) FROM grid.h3_r9;
```

Dokumentacja H3: https://uber.github.io/h3-py/api_quick.html
BDOT10K i agregacje pozostają poza zakresem punktów 1–3.

## Wykonana weryfikacja

Na dostarczonym `powiat.shp` uzyskano 155 komórek dla rozdzielczości 7,
1085 dla 8 i 7589 dla 9. Sprawdzono poprawność geometrii, unikalność ID,
położenie środków w powiecie, powtarzalność wyników i odrzucanie błędnych
rozdzielczości. Wstępny test wykonano przez pyshp i pyproj, a następnie
sprawdzono również odczyt GDAL w kontenerze.
Składnia Python i YAML oraz `docker compose config --quiet` przeszły kontrolę.
Po uruchomieniu Dockera zbudowano obraz i wykonano odczyt GDAL oraz import
do `env-test`, `env-qa` i `main`. Na każdym środowisku sprawdzono w PostGIS:
155, 1085 i 7589 rekordów w tabelach siatek, poprawne geometrie i SRID 4326.
Środowisko testowe działa na porcie 5434, QA na 5435, a main na 5436.
