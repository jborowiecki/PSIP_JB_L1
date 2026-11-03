"""Projekt QGIS: stały zakres IPT [-1,1], nieliniowa skala barw, podgląd r8."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path

# Na Windows platforma offscreen nie zawsze poprawnie renderuje czcionki.
# Aplikacja nie otwiera okien; korzysta z natywnego mechanizmu czcionek.
os.environ['QT_QPA_PLATFORM'] = 'windows' if os.name == 'nt' else 'offscreen'
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFillSymbol,
    QgsGraduatedSymbolRenderer, QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemMap,
    QgsLayoutItemPicture, QgsLayoutPoint, QgsLayoutSize, QgsPrintLayout, QgsProject,
    QgsProperty, QgsReferencedRectangle, QgsRendererRange, QgsSymbolLayer, QgsVectorLayer)
from qgis.PyQt.QtGui import QFont


COLOR_THRESHOLD = 0.0001


def color_position(value):
    """Stałe odwzorowanie barw; nie zmienia wartości IPT ani jego rankingu."""
    clamped = min(1., max(-1., value))
    transformed = math.copysign(math.log1p(abs(clamped)/COLOR_THRESHOLD), clamped)
    return (1 + transformed/math.log1p(1/COLOR_THRESHOLD))/2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    report = json.loads((folder/'weryfikacja_przejezdnosc.json').read_text(encoding='utf-8'))
    config = report['config']
    QgsApplication.setPrefixPath(os.environ['QGIS_PREFIX_PATH'], True)
    app = QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    project.setTitle('Przejezdność terenu — pojazd wojskowy kołowy — powiat sochaczewski')
    expression = ('with_variable(\'v\',clamp(-1,"ipt",1),'
                  'with_variable(\'p\',(1+CASE WHEN @v=0 THEN 0 '
                  f'WHEN @v<0 THEN -ln(1+abs(@v)/{COLOR_THRESHOLD})/ln(1+1/{COLOR_THRESHOLD}) '
                  f'ELSE ln(1+abs(@v)/{COLOR_THRESHOLD})/ln(1+1/{COLOR_THRESHOLD}) END)/2,'
                  'color_rgb(CASE WHEN @p<=0.5 THEN 255 ELSE round(510*(1-@p)) END,'
                  'CASE WHEN @p<=0.5 THEN round(510*@p) ELSE 255 END,'
                  'round(510*CASE WHEN @p<=0.5 THEN @p ELSE 1-@p END))))')
    styling = {'type':'signed_logarithmic_color_scale','threshold':COLOR_THRESHOLD,
               'index_range':[-1,1],'zero_color':[255,255,255],
               'color_stops':[[-1,[255,0,0]],[0,[255,255,255]],[1,[0,255,0]]],
               'same_mapping_for_all_resolutions':True,'index_values_unchanged':True,
               'qgis_expression':expression,
               'formula':'p=(1+sign(IPT)*ln(1+abs(IPT)/0.0001)/ln(10001))/2; RGB=(round(255*min(1,2*(1-p))),round(255*min(1,2*p)),round(510*min(p,1-p)))',
               'gpkg_sha256':hashlib.sha256((folder/'przejezdnosc_3857.gpkg').read_bytes()).hexdigest()}
    (folder/'styl_mapy.json').write_text(json.dumps(styling,ensure_ascii=False,indent=2),encoding='utf-8')
    layers = {}
    for res in [9, 8, 7]:
        layer = QgsVectorLayer(str(folder/'przejezdnosc_3857.gpkg')+f'|layername=h3_r{res}',f'Przejezdność — H3 r{res}','ogr')
        if not layer.isValid():
            raise RuntimeError(f'Niepoprawna warstwa H3 r{res}')
        ranges = []
        bounds = [-1, -.01, -.002, 0, .002, .01, 1]
        # Klasy służą legendzie. Każdy heks otrzymuje ciągły kolor logarytmiczny.
        for start, end in zip(bounds, bounds[1:]):
            position = (color_position(start)+color_position(end))/2
            red = round(255*min(1,2*(1-position)))
            green = round(255*min(1,2*position))
            blue = round(510*min(position,1-position))
            symbol = QgsFillSymbol.createSimple({'color':f'{red},{green},{blue},255', 'outline_style':'no'})
            symbol.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor,QgsProperty.fromExpression(expression))
            ranges.append(QgsRendererRange(start,end,symbol,f'{start:g} do {end:g}'))
        layer.setRenderer(QgsGraduatedSymbolRenderer('ipt',ranges))
        layer.setCustomProperty('przejezdnosc/color_mapping',json.dumps(styling,ensure_ascii=False))
        for field, label in [('ipt','Indeks przejezdności [-1,1]'),
                             ('liczba_kryteriow','Liczba kryteriów w mianowniku'),
                             ('liczba_wag_niezerowych','Liczba wag niezerowych'),
                             ('ipt_min_wrazliwosc','Najniższy IPT przy zmianie wag ±20%'),
                             ('ipt_max_wrazliwosc','Najwyższy IPT przy zmianie wag ±20%'),
                             ('znak_stabilny','Stabilny znak przy zmianie wszystkich wag ±20%'),
                             ('cell_area_m2','Powierzchnia przyciętego heksa [m², EPSG:2180]')]:
            layer.setFieldAlias(layer.fields().indexFromName(field),label)
        for field in layer.fields():
            original = field.name()
            prefix = 'n_' if original.startswith('n_') else 'wk_' if original.startswith('wk_') else ''
            column = original[len(prefix):]
            for key, criterion in config['criteria'].items():
                if column.startswith(key+'_'):
                    unit = column[len(key)+1:]
                    label = ('Normalizacja 0–1: ' if prefix == 'n_' else 'Wkład przed podziałem przez N: ' if prefix == 'wk_' else f'Miara [{unit}]: ')
                    layer.setFieldAlias(layer.fields().indexFromName(original),label+criterion['name']+' ('+key+')')
                    break
        # Indeks i jego wrażliwość na początku tabeli; wszystkie kryteria dostępne dalej.
        columns = layer.attributeTableConfig()
        priority = ['h3_id','resolution','ipt','ipt_min_wrazliwosc','ipt_max_wrazliwosc','znak_stabilny','liczba_kryteriow','liczba_wag_niezerowych','cell_area_m2']
        items = columns.columns()
        items.sort(key=lambda c: priority.index(c.name) if c.name in priority else len(priority))
        columns.setColumns(items)
        layer.setAttributeTableConfig(columns)
        metadata = layer.metadata()
        metadata.setAbstract('Względny model laboratoryjny dla pojazdu wojskowego kołowego. '
            'IPT = suma(waga × miara/maksimum powiatowe)/N. Wagi [-1,1], normalizacja [0,1]. '
            'Wagi zerowe wliczone do N. Maksima osobno dla każdej siatki. '
            'Obliczenia EPSG:2180, geometria EPSG:3857. Stały zakres kolorów [-1,1]. '
            'Skala barw nieliniowa: logarytmiczna ze znakiem i progiem 0,0001, jednakowa dla r7–r9. '
            'Skala nie zmienia indeksu, wag, normalizacji ani rankingu. '
            'Czerwień = -1, biały = 0, zielony = +1. Wagi niekalibrowane terenowo.')
        layer.setMetadata(metadata)
        layer.setMapTipTemplate('<b>[% h3_id %]</b><br>IPT: [% format_number(ipt,6) %]<br>'
                                'Wrażliwość ±20%: [% format_number(ipt_min_wrazliwosc,6) %] do [% format_number(ipt_max_wrazliwosc,6) %]')
        layer.setDisplayExpression('h3_id')
        project.addMapLayer(layer)
        project.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(res == 8)
        layers[res] = layer
    county = QgsVectorLayer(str(folder/'przejezdnosc_3857.gpkg')+'|layername=powiat','Powiat sochaczewski — granica','ogr')
    if not county.isValid():
        raise RuntimeError('Brak granicy powiatu.')
    county.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'255,255,255,0','outline_color':'55,55,55,255','outline_width':'.35'}))
    project.addMapLayer(county)
    extent = county.extent()
    extent.scale(1.06)
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent,project.crs()))
    for table, label in [('kryteria_i_maksima.csv','Kryteria, wagi i maksima'),('analiza_wrazliwosci.csv','Analiza wrażliwości wag')]:
        layer = QgsVectorLayer(str(folder/table),label,'ogr')
        if not layer.isValid():
            raise RuntimeError(f'Niepoprawna tabela: {table}')
        project.addMapLayer(layer)
    scale = folder/'skala_ipt.svg'
    ticks = [(-1,'−1'),(-.01,'−0,01'),(0,'0'),(.01,'+0,01'),(1,'+1')]
    tick_svg = ''.join(f'<line x1="{20+760*color_position(value):.3f}" x2="{20+760*color_position(value):.3f}" y1="30" y2="36" stroke="#222"/>'
                       f'<text x="{20+760*color_position(value):.3f}" y="65">{text}</text>' for value,text in ticks)
    scale.write_text('''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="80" viewBox="0 0 800 80">
      <defs><linearGradient id="r"><stop offset="0" stop-color="#ff0000"/><stop offset="0.5" stop-color="#ffffff"/><stop offset="1" stop-color="#00ff00"/></linearGradient></defs>
      <rect x="20" y="5" width="760" height="25" fill="url(#r)" stroke="#555"/>
      <g font-family="Arial" font-size="28" fill="#222" text-anchor="middle">'''+tick_svg+'</g></svg>',encoding='utf-8')
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.pageCollection().pages()[0].setPageSize('A4',1)
    layout.setName('Przejezdność — mapa H3 r8')
    def label(text,x,y,width,height,size=10):
        item = QgsLayoutItemLabel(layout)
        item.setText(text)
        item.setFont(QFont('Arial',size))
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x,y))
        item.attemptResize(QgsLayoutSize(width,height))
    label('Indeks przejezdności terenu',10,5,280,12,18)
    label('Powiat sochaczewski | pojazd wojskowy kołowy | H3 r8',10,17,280,8,11)
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10,30))
    map_item.attemptResize(QgsLayoutSize(200,155))
    map_item.setCrs(project.crs())
    map_item.setLayers([county,layers[8]])
    map_item.setKeepLayerSet(True)
    map_item.zoomToExtent(extent)
    label('IPT: skala barw nieliniowa',215,35,76,12,11)
    picture = QgsLayoutItemPicture(layout)
    layout.addLayoutItem(picture)
    picture.attemptMove(QgsLayoutPoint(215,50))
    picture.attemptResize(QgsLayoutSize(70,10))
    picture.setPicturePath(str(scale))
    stats = next(grid for grid in report['grids'] if grid['resolution'] == 8)
    label(f"Wynik w tej siatce:\n{stats['ipt_min']:.6f} do {stats['ipt_max']:.6f}\n\n"
          f"Kryteria: {stats['criteria']}\nWagi niezerowe: {stats['nonzero_weights']}\n\n"
          'Normalizacja: 0–1\nWagi: −1 do +1\n\n'
          'Obliczenia: EPSG:2180\nMapa: EPSG:3857\n\n'
          'Skala logarytmiczna\nwspólna dla r7, r8 i r9.\nWartości IPT bez zmian.',215,67,76,101,10)
    label('Model laboratoryjny, wagi bez kalibracji terenowej. Źródło: BDOT10K, GUGiK. Szczegóły w tabelach projektu.',10,190,280,10,9)
    project.layoutManager().addLayout(layout)
    target = folder/'przejezdnosc_3857.qgz'
    if not project.write(str(target)):
        raise RuntimeError('Nie udało się zapisać projektu QGIS.')
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = 150
    settings.exportMetadata = False
    if QgsLayoutExporter(layout).exportToImage(str(folder/'podglad_przejezdnosc_r8.png'),settings) != QgsLayoutExporter.Success:
        raise RuntimeError('Nie udało się wyeksportować podglądu.')
    print(f'Projekt i podgląd QGIS gotowe: {target}')
    project.clear()
    app.exitQgis()


if __name__ == '__main__':
    main()
