from tqdm import tqdm
from os import path
from pyproj import Proj, transform, Transformer
import mercantile
import requests
from vt2geojson.tools import vt_bytes_to_geojson
import os
from shapely.geometry import shape, Point
import shapely

def to_wgs84(proj, easting, northing):
    """
    Transform projected coordinates to WGS84
    Arguments:
        proj: EPSG code of source projection in string format (e.g., epsg:29903)
        easting: source easting
        northing: source northing
    Returns:
        WGS84 latitude, longitude
    """
    transformer = Transformer.from_crs(proj, "epsg:4326")
    return transformer.transform(easting, northing)

def from_wgs84(proj, lat, lng):
    """
    Transform WGS84 lat, lon to projected cooridnates
    Arguments:
        proj: EPSG code of target projection (e.g. 'epsg:29903')
        lat, lng: source WGS84 latutude, longitude
    Returns:
        easting, northing coordinates in the target projection
    """
    transformer = Transformer.from_crs("epsg:4326", proj)
    return transformer.transform(lat, lng)

def proj_transform(transformer, x, y):
    """
    Reproject coordinates using predefined transformer
    Arguments:
        transformer: created using Transformer.from_crs('epsg:<source>', 'epsg:<destination>')
        x, y: orginal coordinates
    Returns:
        reprojected coordinates
    """
    return transformer.transform(x, y)

def bbox_to_wgs84(transformer_to_wgs84, bbox):
    """
    Transform a bounding box to WGS84 coordinate system
    Arguments:
        transformer_to_wgs84: transformer created using Transformer.from_crs(proj, "epsg:4326")
        bbox: [west, south, east, north]
    Returns:
        bbox in WGS84 [lng_min, lat_min, lng_max, lat_max]
    """
    lat1, lng1 = transformer_to_wgs84.transform(bbox[0],bbox[1])
    lat2, lng2 = transformer_to_wgs84.transform(bbox[2],bbox[1])
    lat3, lng3 = transformer_to_wgs84.transform(bbox[0],bbox[3])
    lat4, lng4 = transformer_to_wgs84.transform(bbox[2],bbox[3])
    west = min(lng1, lng2, lng3, lng4)
    east = max(lng1, lng2, lng3, lng4)
    south = min(lat1, lat2, lat3, lat4)
    north = max(lat1, lat2, lat3, lat4)
    return [west, south, east, north]

def fetch_image_info_by_bbox(bbox, proj, access_token):
    """
    Fetch information of images inside an axis-aligned bounding box
    Arguments:
        bbox: bounding box coordinate in projected coordinate system [west, south, east, north]
        proj: EPSG code of projection (e.g. 'epsg:29903')
        access_token: Mapillary API access token
    Returns:
        coordinates in GeoJSON format
    """
    transformer_to_wgs84 = Transformer.from_crs(proj, "epsg:4326")
    transformer_from_wgs84 = Transformer.from_crs("epsg:4326", proj)

    output= { "type": "FeatureCollection", "features": [] }

    tile_coverage = 'mly1_public'
    tile_layer = "image"

    west, south, east, north = bbox_to_wgs84(transformer_to_wgs84, bbox)
    tiles = list(mercantile.tiles(west, south, east, north, 14))
    tile_idx = 0
    for tile in tqdm(tiles):
        tile_url = 'https://tiles.mapillary.com/maps/vtp/{}/2/{}/{}/{}?access_token={}'.format(tile_coverage,tile.z,tile.x,tile.y,access_token)
        response = requests.get(tile_url)
        data = vt_bytes_to_geojson(response.content, tile.x, tile.y, tile.z,layer=tile_layer)

        for feature in data['features']:
            lng = feature['geometry']['coordinates'][0]
            lat = feature['geometry']['coordinates'][1]
            easting, northing = proj_transform(transformer_from_wgs84, lat, lng)

            if easting > bbox[0] and easting < bbox[2] and northing > bbox[1] and northing < bbox[3]:
                output['features'].append(feature)
    return output

def fetch_image_info_by_aoi(aoi, access_token):
    """
    Fetch information of images inside an area of interest
    Arguments:
        aoi: area of interest in shapely polygon format
        proj: EPSG code of projection (e.g. 'epsg:29903')
        access_token: Mapillary API access token
    Returns:
        coordinates in GeoJSON format
    """
    
    output= { "type": "FeatureCollection", "features": [] }

    tile_coverage = 'mly1_public'
    tile_layer = "image"

    west, south, east, north = aoi.bounds
    
    tiles = list(mercantile.tiles(west, south, east, north, 14))
    tile_idx = 0
    
    for tile in tiles:
        tile_idx+=1
        print('tile {}/{}'.format(tile_idx, len(tiles)))
        
        tile_url = 'https://tiles.mapillary.com/maps/vtp/{}/2/{}/{}/{}?access_token={}'.format(tile_coverage,tile.z,tile.x,tile.y,access_token)
        response = requests.get(tile_url)
        data = vt_bytes_to_geojson(response.content, tile.x, tile.y, tile.z,layer=tile_layer)
    
        for feature in tqdm(data['features']):
            lng = feature['geometry']['coordinates'][0]
            lat = feature['geometry']['coordinates'][1]
            point = shapely.geometry.Point(lng,lat)
            if aoi.contains(point):
                output['features'].append(feature)
    return output


def fetch_sequences(odir, img_info, app_access_token, do_not_download=True, max_seqs=None):
    sequences = set()
    for feature in tqdm(img_info['features']):
        seq = feature['properties']['sequence_id']
        sequences.add(seq)

    num_seq = 1
    for seq in sequences:
        if not do_not_download:
            download_sequence(odir, seq, app_access_token)
            num_seq+=1
            if max_seqs != None and num_seq > max_seqs:
                break
    return sequences

def download_sequence(odir, sequence_id, app_access_token):
    try:

        url = 'https://graph.mapillary.com/images?access_token={}&sequence_ids={}'.format(app_access_token, sequence_id)
        # or instead of adding it to the url, add the token in headers (strongly recommended for user tokens)
        headers = {"Authorization": "OAuth {}".format(app_access_token)}
        response = requests.get(url, headers)
        img_data = response.json()

        os.makedirs(os.path.join(odir, sequence_id), exist_ok=True)

        print('downloading {} ...'.format(sequence_id))
        for item in tqdm(img_data['data']):
            image_id = item['id']
            download_image(odir, sequence_id, image_id, app_access_token)

    except:
        with open(os.path.join(odir, 'bad-sequences.list'), 'a') as handler:
            handler.write('{}\n'.format(sequence_id))

def download_image(odir, sequence_id, image_id, app_access_token):
    """
    Download a single image by image ID
    Arguments:
        odir: output directory
        sequence_id: image sequence ID (to organise images by sequence)
        image_id: image ID
        app_access token: Mapillary API access token
    Returns:
        None
    """
    #try:
    url = 'https://graph.mapillary.com/{}?access_token={}&fields=thumb_original_url'.format(image_id,
                                                                                            app_access_token)
    headers = {"Authorization": "OAuth {}".format(app_access_token)}
    response = requests.get(url, headers)
    data = response.json()
    image_url = data['thumb_original_url']

    opath = os.path.join(odir, sequence_id)
    opath = os.path.join(opath, '{}.jpg'.format(image_id))
    if os.path.exists(opath):
        with open(os.path.join(os.path.join(odir, sequence_id), 'already-exists.list'), 'a') as handler:
            handler.write('{}/{}.jpg\n'.format(sequence_id, image_id))
    with open(opath, 'wb') as handler:
        image_data = requests.get(image_url, stream=True).content
        handler.write(image_data)
    #except:
    #    with open(os.path.join(odir, 'bad-images.list'), 'a') as handler:
    #        handler.write('{}/{}.jpg\n'.format(sequence_id, image_id))
            
def get_image_info(image_id, app_access_token):
    """
    Get image information/metadata (e.g., geometry, captured geoemtry, and acquisition time) by image ID
    Arguments:
        image_id: image ID
        app_access_token: Mapillary API access token
    Returns:
        metadata in JSON format
    """
    url = 'https://graph.mapillary.com/{}?access_token={}&fields=geometry,computed_geometry,captured_at'.format(image_id,
                                                                                            app_access_token)
    headers = {"Authorization": "OAuth {}".format(app_access_token)}
    response = requests.get(url, headers)
    data = response.json()
    return data
