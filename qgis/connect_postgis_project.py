"""Projekt QGIS z wszystkimi tabelami laboratoryjnymi bezpośrednio z PostGIS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

os.environ['QT_QPA_PLATFORM'] = 'windows' if os.name == 'nt' else 'offscreen'
from qgis.core import (QgsApplication,QgsDataProvider,QgsDataSourceUri,QgsLayoutExporter,
                      QgsMapLayerStyle,QgsProject,QgsProviderRegistry,QgsSettings,QgsVectorLayer)
from qgis.PyQt.QtCore import QSettings


def connect_project(folder):
    manifest = json.loads((folder/'polaczenie_postgis.json').read_text(encoding='utf-8'))
    config = json.loads((folder/'wagi_modelu.json').read_text(encoding='utf-8'))
    app = QgsApplication([],False)
    QgsApplication.setPrefixPath(os.environ['QGIS_PREFIX_PATH'],True)
    app.initQgis()
    app.setOrganizationName('QGIS')
    app.setApplicationName('QGIS3')
    profile = Path(os.environ['APPDATA'])/'QGIS/QGIS3/profiles/default'
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(profile))
    if Path(QgsSettings().fileName()).resolve() != (profile/'QGIS/QGIS3.ini').resolve():
        raise RuntimeError('Nieoczekiwany profil ustawień QGIS.')
    base = QgsDataSourceUri()
    base.setConnection(manifest['host'],manifest['port'],manifest['database'],manifest['user'],'',QgsDataSourceUri.SslDisable)
    metadata = QgsProviderRegistry.instance().providerMetadata('postgres')
    connection = metadata.createConnection(base.uri(False),{'publicOnly':False,'geometryColumnsOnly':False,'allowGeometrylessTables':True})
    # Test połączenia przed zmianą projektu i zapisem konfiguracji przeglądarki.
    assert 'przejezdnosc' in connection.schemas()
    connection.store(manifest['connection_name'])
    QgsSettings().sync()
    project = QgsProject.instance()
    source = folder/'przejezdnosc_3857.qgz'
    if not project.read(str(source)):
        raise RuntimeError('Nie udało się odczytać dotychczasowego projektu QGIS.')
    original_layers = list(project.mapLayers().values())
    if any(layer.providerType() != 'postgres' for layer in original_layers):
        shutil.copy2(source,folder/'przejezdnosc_offline_3857.qgz')
    report = {'host':manifest['host'],'port':manifest['port'],'database':manifest['database'],
              'connection_name':manifest['connection_name'],'all_analysis_layers_postgres':True,
              'password_in_project':False,'source_table_count':len(manifest['tables']),
              'layers':[],'saved_default_styles':[],'preserved_external_basemaps':[]}
    definitions = {(t['schema'],t['name']):t for t in manifest['tables']}

    def uri_for(schema,table,geom):
        uri = QgsDataSourceUri(base)
        key = definitions[(schema,table)]['key']
        if key:
            key = ','.join('"'+name+'"' for name in key.split(','))
        uri.setDataSource(schema,table,geom,'',key)
        return uri

    def replace(layer,schema,table,geom):
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        options = QgsDataProvider.ProviderOptions()
        options.transformContext = project.transformContext()
        uri = uri_for(schema,table,geom)
        layer.setDataSource(uri.uri(False),layer.name(),'postgres',options,False)
        if not layer.isValid():
            raise RuntimeError(f'Niepoprawna warstwa PostGIS: {schema}.{table} ({geom})')
        style.writeToLayer(layer)
        return layer

    existing = {}
    basemaps = []
    for layer in original_layers:
        if layer.providerType() in ('wms','arcgismapserver','vectortile'):
            basemaps.append(layer)
            report['preserved_external_basemaps'].append(layer.name())
        elif layer.providerType() == 'postgres':
            uri = QgsDataSourceUri(layer.source())
            existing[(uri.schema(),uri.table(),uri.geometryColumn())] = layer
        elif '|layername=' in layer.source():
            table = layer.source().split('|layername=')[1].split('|')[0]
            existing[('przejezdnosc',table,'geom')] = replace(layer,'przejezdnosc',table,'geom')
        elif 'kryteria_i_maksima.csv' in layer.source():
            existing[('przejezdnosc','kryteria','')] = replace(layer,'przejezdnosc','kryteria','')
        elif 'analiza_wrazliwosci.csv' in layer.source():
            existing[('przejezdnosc','wrazliwosc','')] = replace(layer,'przejezdnosc','wrazliwosc','')
        else:
            raise RuntimeError(f'Nieznane źródło warstwy: {layer.name()}')
    root = project.layerTreeRoot()
    order = [('przejezdnosc','Przejezdność — wyniki EPSG:3857'),
             ('tabele','Tabele — kryteria, wagi, wrażliwość i kontrole'),
             ('bdot','BDOT10K — dane źródłowe EPSG:2180'),
             ('analiza','BDOT10K — agregaty robocze EPSG:2180'),
             ('pelne','Pełne heksy pomocnicze EPSG:2180'),
             ('przejezdnosc_robocza','Przejezdność — obliczenia EPSG:2180'),
             ('wyniki','BDOT10K — agregaty wynikowe EPSG:3857'),
             ('grid','Pierwotne siatki H3 EPSG:4326'),
             ('public','Granica źródłowa powiatu'),
             ('podklad','Podkład mapowy — usługi zewnętrzne')]
    groups = {}
    for key,label in order:
        group = root.findGroup(label) or root.addGroup(label)
        group.setExpanded(key in ('przejezdnosc','tabele'))
        groups[key] = group
    for layer in basemaps:
        node = root.findLayer(layer.id())
        if node and node.parent()!=groups['podklad']:
            clone = node.clone()
            groups['podklad'].addChildNode(clone)
            node.parent().removeChildNode(node)
    def register(layer,schema,table,geom):
        if not layer.isValid() or layer.featureCount() < 0:
            raise RuntimeError(f'Nie można odczytać {schema}.{table}')
        if layer.providerType() != 'postgres':
            raise AssertionError('Projekt zawiera źródło plikowe.')
        uri = QgsDataSourceUri(layer.source())
        if uri.password():
            raise AssertionError('Hasło znalazło się w źródle warstwy.')
        group_key = 'tabele' if not geom else 'pelne' if geom == 'full_geom' else schema
        node = root.findLayer(layer.id())
        if node:
            if node.parent() != groups[group_key]:
                clone = node.clone()
                groups[group_key].addChildNode(clone)
                node.parent().removeChildNode(node)
        else:
            project.addMapLayer(layer,False)
            groups[group_key].addLayer(layer)
        root.findLayer(layer.id()).setItemVisibilityChecked(schema=='przejezdnosc' and table in ('h3_r8','powiat') and geom=='geom')
        layer.setReadOnly(True)
        layer.setCustomProperty('psip/schema',schema)
        layer.setCustomProperty('psip/table',table)
        layer.setCustomProperty('psip/geometry_column',geom)
        layer.setCustomProperty('psip/source','PostGIS env-test Docker')
        if schema=='przejezdnosc' and geom=='geom':
            message = layer.saveStyleToDatabase('PSIP_'+table,
                'Styl laboratoryjny: IPT -1 czerwony, 0 biały, +1 zielony; nieliniowa skala barw.',True,'')
            if message:
                raise RuntimeError(f'Nie udało się zapisać stylu {table}: {message}')
            report['saved_default_styles'].append(schema+'.'+table)
        report['layers'].append({'schema':schema,'table':table,'geometry_column':geom,
                                 'features':layer.featureCount(),'epsg':layer.crs().postgisSrid() if geom else None,
                                 'provider':layer.providerType(),'valid':True})
    for table in manifest['tables']:
        schema,name = table['schema'],table['name']
        geometries = table['geometry'] or [{'column':''}]
        for geometry in geometries:
            geom = geometry['column']
            key = (schema,name,geom)
            layer = existing.get(key)
            if layer is None:
                label = schema+'.'+name
                if schema=='bdot':
                    label = name+' — '+config['criteria'][name]['name']
                elif geom=='full_geom':
                    label = 'Pełny heks — '+name
                layer = QgsVectorLayer(uri_for(schema,name,geom).uri(False),label,'postgres')
            register(layer,schema,name,geom)
    # Granica na wierzchu, pod nią wybrana siatka. Pozostałe warstwy wyłączone.
    primary = groups['przejezdnosc']
    for table in ['h3_r7','h3_r9','h3_r8','powiat']:
        layer = existing.get(('przejezdnosc',table,'geom'))
        if layer:
            node = primary.findLayer(layer.id())
            clone = node.clone()
            primary.insertChildNode(0,clone)
            primary.removeChildNode(node)
    project.setTitle('Przejezdność i BDOT10K — PostGIS env-test Docker — powiat sochaczewski')
    project.setCustomVariables({**project.customVariables(),'psip_database':'psip','psip_host':'localhost','psip_port':5434})
    target = folder/'przejezdnosc_postgis.qgz'
    if not project.write(str(target)):
        raise RuntimeError('Nie udało się zapisać projektu PostGIS.')
    shutil.copy2(target,source)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = 150
    settings.exportMetadata = False
    layout = project.layoutManager().layouts()[0]
    if QgsLayoutExporter(layout).exportToImage(str(folder/'podglad_przejezdnosc_postgis_r8.png'),settings) != QgsLayoutExporter.Success:
        raise RuntimeError('Nie udało się wyrenderować mapy z bazy.')
    report['layer_count'] = len(report['layers'])
    report['bdot_layer_count'] = sum(row['schema']=='bdot' for row in report['layers'])
    report['project_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (folder/'kontrola_polaczenia_postgis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Projekt PostGIS: {target}; warstw i tabel w projekcie: {report["layer_count"]}; BDOT10K: {report["bdot_layer_count"]}')
    project.clear()
    app.exitQgis()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    args = parser.parse_args()
    connect_project(args.folder.resolve())


if __name__ == '__main__':
    main()
