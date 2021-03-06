# -*- coding: utf-8 -*-
"""

Authors
-------
John Weaver <john.weaver.astro@gmail.com>


About
-----
Functions to handle command-line input

Known Issues
------------
None


"""

# General imports
import os
import sys
import time
from functools import partial
sys.path.insert(0, os.path.join(os.getcwd(), 'config'))

# Tractor imports
from tractor import NCircularGaussianPSF, PixelizedPSF, Image, Tractor, FluxesPhotoCal, NullWCS, ConstantSky, EllipseESoft, Fluxes, PixPos
from tractor.galaxy import ExpGalaxy, DevGalaxy, FixedCompositeGalaxy, SoftenedFracDev, GalaxyShape
from tractor.pointsource import PointSource
from tractor.psfex import PixelizedPsfEx, PsfExModel
from tractor.psf import HybridPixelizedPSF

# Miscellaneous science imports
from astropy.io import fits, ascii
from astropy.table import Table, Column, vstack, join
from astropy.wcs import WCS
import astropy.units as u
import numpy as np
from functools import partial
import matplotlib.pyplot as plt
import weakref
from scipy import stats
import pathos as pa
from astropy.coordinates import SkyCoord
# import sfdmap

# Local imports
from .brick import Brick
from .mosaic import Mosaic
from .utils import header_from_dict, SimpleGalaxy
from .visualization import plot_background, plot_blob, plot_blobmap, plot_brick, plot_mask
try:
    import config as conf
except:
    raise RuntimeError('Cannot find configuration file!')

# m = sfdmap.SFDMap(conf.SFDMAP_DIR)

# Make sure no interactive plotting is going on.
plt.ioff()
import warnings
warnings.filterwarnings("ignore")

print(
f"""
******** F A R M E R ********
(c) J. Weaver (DAWN, Univ. of Copenhagen)

CONSOLE_LOGGING_LEVEL ..... {conf.CONSOLE_LOGGING_LEVEL}			
LOGFILE_LOGGING_LEVEL ..... {conf.LOGFILE_LOGGING_LEVEL}												
PLOT ...................... {conf.PLOT}																		
NTHREADS .................. {conf.NTHREADS}																			
OVERWRITE ................. {conf.OVERWRITE} 
"""	
)

print('Starting up logging system...')

# Start the logging
import logging.config
logger = logging.getLogger('farmer')

if not len(logger.handlers):
    if conf.LOGFILE_LOGGING_LEVEL is not None:
        logging_level = logging.getLevelName(conf.LOGFILE_LOGGING_LEVEL)
    else:
        logging_level = logging.DEBUG
    logger.setLevel(logging_level)
    formatter = logging.Formatter('[%(asctime)s] %(name)s :: %(levelname)s - %(message)s', '%H:%M:%S')

    # Logging to the console at logging level
    ch = logging.StreamHandler()
    ch.setLevel(logging.getLevelName(conf.CONSOLE_LOGGING_LEVEL))
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    if conf.LOGFILE_LOGGING_LEVEL is None:
        print('Logging information wills stream only to console.\n')
        
    else:
        # create file handler which logs even debug messages
        logging_path = os.path.join(conf.LOGGING_DIR, 'logfile.log')
        print(f'Logging information will stream to console and {logging_path}\n')
        # If overwrite is on, remove old logger
        if conf.OVERWRITE & os.path.exists(logging_path):
            print('WARNING -- Existing logfile will be overwritten.')
            os.remove(logging_path)

        fh = logging.FileHandler(logging_path)
        fh.setLevel(logging.getLevelName(conf.LOGFILE_LOGGING_LEVEL))
        fh.setFormatter(formatter)
        logger.addHandler(fh)



# The logo
logger.info(
"""
====================================================================
 ________    _       _______     ____    ____  ________  _______        
|_   __  |  / \     |_   __ \   |_   \  /   _||_   __  ||_   __ \    
  | |_ \_| / _ \      | |__) |    |   \/   |    | |_ \_|  | |__) |   
  |  _|   / ___ \     |  __ /     | |\  /| |    |  _| _   |  __ /    
 _| |_  _/ /   \ \_  _| |  \ \_  _| |_\/_| |_  _| |__/ | _| |  \ \_ 
|_____||____| |____||____| |___||_____||_____||________||____| |___|
                                                                    
--------------------------------------------------------------------
M O D E L   P H O T O M E T R Y   W I T H   T H E   T R A C T O R   
--------------------------------------------------------------------
                                                                    
    (C) 2019 -- J. Weaver (DAWN, University of Copenhagen)          
====================================================================
"""
)



# When a user invokes the interface, first check the translation file
# Optionally, tell the user.
# Try to import the translate file from it's usual spot first.
try:
    from translate import translate
    logger.info(f'interface.translation :: Imported translate file with {len(translate.keys())} entries.')
    if len(conf.BANDS) != len(translate.keys()):
        logger.warning(f'Configuration file only includes {len(conf.BANDS)} entries!')
    # I have nicknames in the config, I need the raw names for file I/O
    mask = np.ones_like(conf.BANDS, dtype=bool)
    for i, band in enumerate(conf.BANDS):
        if band not in translate.keys():
            logger.warning(f'Cound not find {band} in translate file!')
            mask[i] = False

    # Re-assign bands and rawbands in config object
    logger.debug(f'Assigning nicknames to raw image names:')
    conf.BANDS = list(np.array(conf.BANDS)[mask])
    conf.RAWBANDS = conf.BANDS.copy()
    for i, band in enumerate(conf.RAWBANDS):
        conf.RAWBANDS[i] = translate[band]
        logger.debug(f'     {i+1} :: {conf.RAWBANDS[i]} --> {conf.BANDS[i]}')

# The translation file could not be found, so make a scene.
except:
    logger.warning('interface.translation :: WARNING - Could not import translate file! Will use config instead.')
    logger.info('interface.translation :: Image names must be < 50 characters (FITS standard) - checking...')
    # I have raw names, I need shortened raw names (i.e. nicknames)
    conf.RAWBANDS = conf.BANDS.copy()
    count_short = 0
    for i, band in enumerate(conf.RAWBANDS):
        if len(band) > 50:  
            conf.BANDS[i] = band[:50]
            logger.debug(f'     {i+1} :: {band} --> {conf.BANDS[i]}')
            count_short += 1
    logger.info(f'interface.translation :: Done checking. Shortened {count_short} image names.')


def make_psf(image_type=conf.MULTIBAND_NICKNAME, band=None, sextractor_only=False, psfex_only=False, override=conf.OVERWRITE):
    """ This is where we automatically construct the PSFs for Farmer.

    Step 1. Run sextractor_only=True to obtain the PSF candidates
    Step 2. Using the output plot, determine the selection box for the stars
    Step 3. Run psfex_only=True to construct the PSF.

    See config file to set box dimensions, psf spatial sampling, etc.

    """

    # If the user asked to make a PSF for the detection image, tell them we don't do that
    if image_type is conf.DETECTION_NICKNAME:
        raise ValueError('Farmer does not use a PSF to perform detection!')

    # Else if the user asks for a PSF to be made for the modeling band
    elif image_type is conf.MODELING_NICKNAME:
        # Make the Mosaic
        logger.info(f'Making PSF for {conf.MODELING_NICKNAME}')
        modmosaic = Mosaic(conf.MODELING_NICKNAME, modeling=True, mag_zeropoint=conf.MODELING_ZPT)

        # Make the PSF
        logger.info(f'Mosaic loaded for {conf.MODELING_NICKNAME}')
        modmosaic._make_psf(xlims=conf.MOD_REFF_LIMITS, ylims=conf.MOD_VAL_LIMITS, override=override, sextractor_only=sextractor_only, psfex_only=psfex_only)

        logger.info(f'PSF made successfully for {conf.MODELING_NICKNAME}')

    # Else if the user asks for a PSF in one of the bands
    elif image_type is conf.MULTIBAND_NICKNAME:
        
        # Sanity check
        if band not in conf.BANDS:
            raise ValueError(f'{band} is not a valid band nickname!')

        # Use all bands or just one?
        if band is not None:
            sbands = [band,]
        else:
            sbands = conf.BANDS

        # Loop over bands
        for i, band in enumerate(sbands):

            # Figure out PS selection box position and zeropoint
            idx_band = np.array(conf.BANDS) == band
            multi_xlims = np.array(conf.MULTIBAND_REFF_LIMITS)[idx_band][0]
            multi_ylims = np.array(conf.MULTIBAND_VAL_LIMITS)[idx_band][0]
            mag_zpt = np.array(conf.MULTIBAND_ZPT)[idx_band][0]

            # Make the Mosaic
            logger.info(f'Making PSF for {band}')
            bandmosaic = Mosaic(band, mag_zeropoint = mag_zpt)

            # Make the PSF
            logger.info(f'Mosaic loaded for {band}')
            bandmosaic._make_psf(xlims=multi_xlims, ylims=multi_ylims, override=override, sextractor_only=sextractor_only, psfex_only=psfex_only)

            if not sextractor_only:
                logger.info(f'PSF made successfully for {band}')
            else:
                logger.info(f'interface.make_psf :: SExtraction complete for {band}')
    
    return


def make_bricks(image_type=conf.MULTIBAND_NICKNAME, band=None, insert=False, skip_psf=True):
    """ Stage 1. Here we collect the detection, modelling, and multiband images for processing. We may also cut them up! 
    
    NB: PSFs can be automatically made at this stage too, assuming you've determined your PSF selection a priori.
    
    """

    # Make bricks for the detection image
    if (image_type==conf.DETECTION_NICKNAME) | (image_type is None):
        # Detection
        logger.info('Making mosaic for detection...')
        detmosaic = Mosaic(conf.DETECTION_NICKNAME, detection=True)

        if conf.NTHREADS > 0:
            logger.warning('Parallelization of brick making is currently disabled')
            # BUGGY DUE TO MEM ALLOC
            # logger.info('Making bricks for detection (in parallel)')
            # pool = mp.ProcessingPool(processes=conf.NTHREADS)
            # pool.map(partial(detmosaic._make_brick, detection=True, overwrite=True), np.arange(0, detmosaic.n_bricks()))

        logger.info('Making bricks for detection (in serial)')
        for brick_id in np.arange(1, detmosaic.n_bricks()+1):
            detmosaic._make_brick(brick_id, detection=True, overwrite=True)

    # Make bricks for the modeling image
    elif (image_type==conf.MODELING_NICKNAME) | (image_type is None):
        # Modeling
        logger.info('Making mosaic for modeling...')
        modmosaic = Mosaic(conf.MODELING_NICKNAME, modeling=True)

        # The user wants PSFs on the fly
        if not skip_psf: 

            mod_xlims = np.array(conf.MOD_REFF_LIMITS)
            mod_ylims = np.array(conf.MOD_VAL_LIMITS)
                
            modmosaic._make_psf(xlims=mod_xlims, ylims=mod_ylims)

        # Make bricks in parallel
        if conf.NTHREADS > 0:
            logger.warning('Parallelization of brick making is currently disabled')
            # BUGGY DUE TO MEM ALLOC
            # if conf.VERBOSE: print('Making bricks for detection (in parallel)')
            # pool = mp.ProcessingPool(processes=conf.NTHREADS)
            # pool.map(partial(modmosaic._make_brick, detection=True, overwrite=True), np.arange(0, modmosaic.n_bricks()))

        # Make bricks in serial
        else:
            logger.info('Making bricks for modeling (in serial)')
            for brick_id in np.arange(1, modmosaic.n_bricks()+1):
                modmosaic._make_brick(brick_id, modeling=True, overwrite=True)
    
    # Make bricks for one or more multiband images
    elif (image_type==conf.MULTIBAND_NICKNAME) | (image_type is None):

        # One variable list
        if band is not None:
            sbands = [band,]
        else:
            sbands = conf.BANDS

        # In serial, loop over images
        for i, sband in enumerate(sbands):

            # Assume we can overwrite files unless insertion is explicit
            # First image w/o insertion will make new file anyways
            overwrite = True
            if insert | (i > 0):
                overwrite = False

            # Build the mosaic
            logger.info(f'Making mosaic for image {sband}...')
            bandmosaic = Mosaic(sband)

            # The user wants PSFs made on the fly
            if not skip_psf: 

                idx_band = np.array(conf.BANDS) == sband
                multi_xlims = np.array(conf.MULTIBAND_REFF_LIMITS)[idx_band][0]
                multi_ylims = np.array(conf.MULTIBAND_VAL_LIMITS)[idx_band][0]
                    
                bandmosaic._make_psf(xlims=multi_xlims, ylims=multi_ylims)

            # Make bricks in parallel
            if conf.NTHREADS > 0:
                logger.info(f'Making bricks for band {sband} (in parallel)')
                with pa.pools.ProcessPool(ncpus=conf.NTHREADS) as pool:
                    logger.info(f'Parallel processing pool initalized with {conf.NTHREADS} threads.')
                    pool.uimap(partial(bandmosaic._make_brick, detection=False, overwrite=overwrite), np.arange(0, bandmosaic.n_bricks()))
                    logger.info('Parallel processing complete.')
            # Make bricks in serial
            else:
                logger.info(f'Making bricks for band {sband} (in serial)')
                for brick_id in np.arange(1, bandmosaic.n_bricks()+1):
                    bandmosaic._make_brick(brick_id, detection=False, overwrite=overwrite)

    # image type is invalid
    else:
        raise RuntimeError(f'{image_type} is an unrecognized nickname (see {conf.DETECTION_NICKNAME}, {conf.MODELING_NICKNAME}, {conf.MULTIBAND_NICKNAME})')

    return

        
def runblob(blob_id, blobs, modeling=None, catalog=None, plotting=0, source_id=None, source_only=False):
    """ Essentially a private function. Runs each individual blob and handles the bulk of the work. """

    # if conf.NTHREADS != 0:
    #     fh = logging.FileHandler(f'B{blob_id}.log')
    #     fh.setLevel(logging.getLevelName(conf.LOGFILE_LOGGING_LEVEL))
    #     formatter = logging.Formatter('[%(asctime)s] %(name)s :: %(levelname)s - %(message)s', '%H:%M:%S')
    #     fh.setFormatter(formatter)

    #     logger = pathos.logger(level=logging.getLevelName(conf.LOGFILE_LOGGING_LEVEL), handler=fh)

    logger = logging.getLogger(f'farmer.blob.{blob_id}')
    logger.info(f'Starting on Blob #{blob_id}')

    modblob = None
    fblob = None
    tstart = time.time()
    logger.debug('Making weakref proxies of blobs')
    if modeling is None:
        modblob, fblob = weakref.proxy(blobs[0]), weakref.proxy(blobs[1])
    elif modeling:
        modblob = weakref.proxy(blobs)
    else:
        fblob = weakref.proxy(blobs)
    logger.debug(f'Weakref made ({time.time() - tstart:3.3f})s')


    # Make blob with modeling image 
    if modblob is not None:
        logger.debug(f'Making blob with {conf.MODELING_NICKNAME}')
        modblob.logger = logger

        if modblob.rejected:
            logger.info('Blob has been rejected!')
            # if conf.NTHREADS != 0:
            #     logger.removeHandler(fh)
            catout = modblob.bcatalog.copy()
            del modblob
            return catout

        # If the user wants to just model a specific source...
        if source_only & (source_id is not None):
            logger.info(f'Preparing to model single source: {source_id}')
            sid = modblob.bcatalog['source_id']
            modblob.bcatalog = modblob.bcatalog[sid == source_id]
            modblob.n_sources = len(modblob.bcatalog)
            modblob.mids = np.ones(modblob.n_sources, dtype=int)
            modblob.model_catalog = np.zeros(modblob.n_sources, dtype=object)
            modblob.solution_catalog = np.zeros(modblob.n_sources, dtype=object)
            modblob.solved_chisq = np.zeros(modblob.n_sources)
            modblob.solved_bic = np.zeros(modblob.n_sources)
            modblob.solution_chisq = np.zeros(modblob.n_sources)
            modblob.tr_catalogs = np.zeros((modblob.n_sources, 3, 2), dtype=object)
            modblob.chisq = np.zeros((modblob.n_sources, 3, 2))
            modblob.rchisq = np.zeros((modblob.n_sources, 3, 2))
            modblob.bic = np.zeros((modblob.n_sources, 3, 2))
            assert(len(modblob.bcatalog) > 0)

        if (conf.MODEL_PHOT_MAX_NBLOB > 0) & (modblob.n_sources > conf.MODEL_PHOT_MAX_NBLOB):
            logger.info('Number of sources exceeds set limit. Skipping!')
            # if conf.NTHREADS != 0:
            #     logger.removeHandler(fh)
            catout = modblob.bcatalog.copy()
            catout['x'] += modblob.subvector[1]
            catout['y'] += modblob.subvector[0]
            del modblob
            return catout

        # Run models
        astart = time.time()
        logger.debug(f'Staging images for {conf.MODELING_NICKNAME}')
        modblob.stage_images()
        logger.debug(f'Images staged. ({time.time() - astart:3.3f})s')

        astart = time.time()
        logger.debug(f'Modeling images for {conf.MODELING_NICKNAME}')
        status = modblob.tractor_phot()

        if not status:
            logger.warning(f'Morphology failed! ({time.time() - astart:3.3f})s')

            # if conf.NTHREADS != 0:
            #     logger.removeHandler(fh)
            catout = modblob.bcatalog.copy()
            catout['x'] += modblob.subvector[1]
            catout['y'] += modblob.subvector[0]
            del modblob
            return catout

        logger.debug(f'Morphology determined. ({time.time() - astart:3.3f})s')


        # Run follow-up phot
        if conf.DO_APPHOT:
            for img_type in ('image', 'model', 'isomodel', 'residual'):
                # for band in modblob.bands:
                # try:
                modblob.aperture_phot(image_type=img_type, sub_background=conf.SUBTRACT_BACKGROUND)
                # except:
                #     logger.info(f'interface.runblob :: WARNING - Aperture photmetry FAILED for {conf.MODELING_NICKNAME} {img_type}')
        if conf.DO_SEXPHOT:
            try:
                modblob.sextract_phot()
            except:
                logger.warning(f'Source extraction on the residual blob FAILED for {conf.MODELING_NICKNAME} {img_type}')    

        duration = time.time() - tstart
        logger.info(f'Solution for Blob #{modblob.blob_id} (N={modblob.n_sources}) arrived at in {duration:3.3f}s ({duration/modblob.n_sources:2.2f}s per src)')
    
        catout = modblob.bcatalog.copy()
        del modblob

    if fblob is not None:
        # make new blob with band information
        logger.debug(f'Making blob with {conf.MULTIBAND_NICKNAME}')
        fblob.logger = logger

        astart = time.time() 
        if modblob is not None:
            fblob.model_catalog = modblob.solution_catalog.copy()
            fblob.position_variance = modblob.position_variance.copy()
            fblob.parameter_variance = modblob.parameter_variance.copy()
            logger.info(f'Solution parameters transferred. ({time.time() - astart:3.3f})s')

        else:
            if catalog is None:
                raise ValueError('Input catalog not supplied!')
            else:
                blobmask = np.ones(len(catalog))
                if source_id is not None:
                    # If the user wants to just model a specific source...
                    logger.info(f'Preparing to force single source: {source_id}')
                    sid = catalog['source_id']
                    bid = catalog['blob_id']
                    fblob.bcatalog = catalog[(sid == source_id) & (bid == blob_id)]
                    fblob.n_sources = len(fblob.bcatalog)
                    fblob.mids = np.ones(fblob.n_sources, dtype=int)
                    fblob.model_catalog = np.zeros(fblob.n_sources, dtype=object)
                    fblob.solution_catalog = np.zeros(fblob.n_sources, dtype=object)
                    fblob.solved_chisq = np.zeros(fblob.n_sources)
                    fblob.solved_bic = np.zeros(fblob.n_sources)
                    fblob.solution_chisq = np.zeros(fblob.n_sources)
                    fblob.tr_catalogs = np.zeros((fblob.n_sources, 3, 2), dtype=object)
                    fblob.chisq = np.zeros((fblob.n_sources, 3, 2))
                    fblob.rchisq = np.zeros((fblob.n_sources, 3, 2))
                    fblob.bic = np.zeros((fblob.n_sources, 3, 2))
                    assert(len(fblob.bcatalog) > 0)
                else:
                    if blob_id is not None:
                        blobmask = catalog['blob_id'] == blob_id
                    fblob.bcatalog = catalog[blobmask]
                    fblob.n_sources = len(fblob.bcatalog)
                    catalog = catalog[blobmask]

                catalog['X_MODEL'] -= fblob.subvector[1] + fblob.mosaic_origin[1] - conf.BRICK_BUFFER + 1
                catalog['Y_MODEL'] -= fblob.subvector[0] + fblob.mosaic_origin[0] - conf.BRICK_BUFFER + 1

                fblob.model_catalog, good_sources = models_from_catalog(catalog, fblob)
                if (good_sources == False).all():
                    logger.warning('All sources are invalid!')
                    catalog['X_MODEL'] += fblob.subvector[1] + fblob.mosaic_origin[1] - conf.BRICK_BUFFER + 1
                    catalog['Y_MODEL'] += fblob.subvector[0] + fblob.mosaic_origin[0] - conf.BRICK_BUFFER + 1
                    return catalog

                fblob.position_variance = None
                fblob.parameter_variance = None
                fblob.bcatalog = catalog[good_sources]
                fblob.n_sources = len(catalog)

        if fblob.rejected:
            logger.info('Blob has been rejected!')
            # if conf.NTHREADS != 0:
            #     logger.removeHandler(fh)
            catout = fblob.bcatalog.copy()
            del fblob
            return catout

        # Forced phot
        astart = time.time() 
        status = fblob.stage_images()
        if not status:
            # if conf.NTHREADS != 0:
            #     logger.removeHandler(fh)
            catout = fblob.bcatalog.copy()
            del fblob
            return catout
            
        logger.info(f'{len(fblob.bands)} images staged. ({time.time() - astart:3.3f})s')

        astart = time.time() 
        logger.info(f'Starting forced photometry...')
        status = fblob.forced_phot()

        if not status:
            # if conf.NTHREADS != 0:
            #     logger.removeHandler(fh)
            catout = fblob.bcatalog.copy()
            del fblob
            return catout

        logger.info(f'Force photometry complete. ({time.time() - astart:3.3f})s')


        # Run follow-up phot
        if conf.DO_APPHOT:
            for img_type in ('image', 'model', 'isomodel', 'residual',):
                for band in fblob.bands:
                    try:
                        fblob.aperture_phot(band, img_type, sub_background=conf.SUBTRACT_BACKGROUND)
                    except:
                        logger.warning(f'Aperture photmetry FAILED for {band} {img_type}. Likely a bad blob.')
        if conf.DO_SEXPHOT:
            try:
                [fblob.sextract_phot(band) for band in fblob.bands]
            except:
                logger.warning(f'Residual Sextractor photmetry FAILED. Likely a bad blob.)')

        duration = time.time() - tstart
        logger.info(f'Solution for blob {fblob.blob_id} (N={fblob.n_sources}) arrived at in {duration:3.3f}s ({duration/fblob.n_sources:2.2f}s per src)')


        catout = fblob.bcatalog.copy()
        del fblob

    # if conf.NTHREADS != 0:
    #     logger.removeHandler(fh)
    return catout


def make_models(brick_id, band=conf.MODELING_NICKNAME, source_id=None, blob_id=None, segmap=None, blobmap=None, catalog=None, use_mask=True, source_only=False):
    """ Stage 2. Detect your sources and determine the best model parameters for them """

    # Create detection brick
    tstart = time.time()

    if (source_id is None) & (blob_id is None):
        if (conf.NBLOBS == 0) & (conf.NTHREADS > 0) & ((conf.PLOT > 0)):
            conf.PLOT = 0
            logger.warning('Plotting not supported while modeling in parallel!')


    detbrick = stage_brickfiles(brick_id, nickname=conf.DETECTION_NICKNAME, modeling=True)

    logger.info(f'Detection brick #{brick_id} created ({time.time() - tstart:3.3f}s)')

    # Sextract sources 
    tstart = time.time()
    if (segmap is None) & (catalog is None):
        try:
            detbrick.sextract(conf.DETECTION_NICKNAME, sub_background=conf.DETECTION_SUBTRACT_BACKGROUND, use_mask=use_mask, incl_apphot=conf.DO_APPHOT)
            logger.info(f'Detection brick #{brick_id} sextracted {detbrick.n_sources} objects ({time.time() - tstart:3.3f}s)')
            is_borrowed = False
        except:
            raise RuntimeError(f'Detection brick #{brick_id} sextraction FAILED. ({time.time() - tstart:3.3f}s)')
            return

    # or find existing catalog/segmap info
    elif (catalog == 'auto') | ((segmap is not None) & (catalog is not None) & (segmap is not None)):
        if (catalog == 'auto'):
            search_fn = os.path.join(conf.CATALOG_DIR, f'B{brick_id}.cat')
            if os.path.exists(search_fn):
                catalog = Table(fits.open(search_fn)[1].data)
            else:
                raise ValueError(f'No valid catalog was found for {brick_id}')
            logger.info(f'Overriding SExtraction with external catalog. ({search_fn})')
            search_fn = os.path.join(conf.INTERIM_DIR, f'B{brick_id}_SEGMAPS.fits')
            if os.path.exists(search_fn):
                hdul_seg = fits.open(search_fn)
                segmap = hdul_seg['SEGMAP'].data
                blobmap = hdul_seg['BLOBMAP'].data
            else:
                raise ValueError(f'No valid segmentation map was found for {brick_id}')
        if 'x' in catalog.colnames:
            catalog['x'].name = 'x_orig'
        if 'y' in catalog.colnames:
            catalog['y'].name = 'y_orig'
        catalog[conf.X_COLNAME].name = 'x'
        catalog[conf.Y_COLNAME].name = 'y'
        catalog['x'] = catalog['x'] - detbrick.mosaic_origin[1] + conf.BRICK_BUFFER - 1
        catalog['y'] = catalog['y'] - detbrick.mosaic_origin[0] + conf.BRICK_BUFFER - 1
        detbrick.catalog = catalog
        detbrick.n_sources = len(catalog)
        detbrick.n_blobs = catalog['blob_id'].max()
        is_borrowed = True
        detbrick.segmap = segmap
        detbrick.blobmap = blobmap
        
    else:
        raise ValueError('No valid segmap, blobmap, and catalog provided to override SExtraction!')
        return

    # Create modbrick
    if band == conf.MODELING_NICKNAME:
        img_names = [conf.MODELING_NICKNAME,]
        mod_nickname = band 

    else:
        img_names = [band,]
        mod_nickname = conf.MULTIBAND_NICKNAME

    # Loop over bands to do the modelling on
    for band_num, mod_band in enumerate(img_names):
        tstart = time.time()
        if band_num > 0:
            n_blobs, n_sources, segmap, segmask, blobmap, catalog = modbrick.n_blobs, modbrick.n_sources, modbrick.segmap, modbrick.segmask, modbrick.blobmap, modbrick.catalog
            catalog['x'] = catalog['x'] - detbrick.mosaic_origin[1] + conf.BRICK_BUFFER - 1
            catalog['y'] = catalog['y'] - detbrick.mosaic_origin[0] + conf.BRICK_BUFFER - 1
        modbrick = stage_brickfiles(brick_id, band=mod_band, nickname=mod_nickname, modeling=True)
        if band != conf.MODELING_NICKNAME:
            modbrick.bands = [f'{conf.MODELING_NICKNAME}_{mod_band}',]
            modbrick.n_bands = len(modbrick.bands)
        else:
            mod_band = conf.MODELING_NICKNAME
        logger.info(f'Modeling brick #{brick_id} created ({time.time() - tstart:3.3f}s)')


        if conf.PLOT > 2:
            plot_brick(modbrick, 0, band=mod_band)
            plot_background(modbrick, 0, band=mod_band)
            plot_mask(modbrick, 0, band=mod_band)

        logger.debug(f'Brick #{brick_id} -- Image statistics for {mod_band}')
        shape, minmax, mean, var = stats.describe(modbrick.images[0], axis=None)[:4]
        logger.debug(f'    Limits: {minmax[0]:3.3f} - {minmax[1]:3.3f}')
        logger.debug(f'    Mean: {mean:3.3f}+/-{np.sqrt(var):3.3f}\n')
        logger.debug(f'Brick #{brick_id} -- Weight statistics for {mod_band}')
        shape, minmax, mean, var = stats.describe(modbrick.weights[0], axis=None)[:4]
        logger.debug(f'    Limits: {minmax[0]:3.3f} - {minmax[1]:3.3f}')
        logger.debug(f'    Mean: {mean:3.3f}+/-{np.sqrt(var):3.3f}\n')
        logger.debug(f'Brick #{brick_id} -- Background statistics for {mod_band}')
        logger.debug(f'    Global: {modbrick.backgrounds[0, 0]:3.3f}')
        logger.debug(f'    RMS: {modbrick.backgrounds[0, 1]:3.3f}\n')

        modbrick.catalog = detbrick.catalog.copy()
        modbrick.segmap = detbrick.segmap
        modbrick.n_sources = detbrick.n_sources
        if is_borrowed:
            modbrick.blobmap = detbrick.blobmap

        # Cleanup on MODBRICK
        tstart = time.time()
        if (~is_borrowed) & (band_num == 0):
            modbrick.cleanup()
        if band_num > 0:
            modbrick.n_blobs, modbrick.n_sources, modbrick.segmap, modbrick.segmask, modbrick.blobmap, modbrick.catalog = n_blobs, n_sources, segmap, segmask, blobmap, catalog
        modbrick.add_columns(modbrick_name=mod_band) # doing on detbrick gets column names wrong
        logger.info(f'Modeling brick #{brick_id} gained {modbrick.n_blobs} blobs with {modbrick.n_sources} objects ({time.time() - tstart:3.3f}s)')

        if conf.PLOT > 2:
            plot_blobmap(modbrick)
            plot_blobmap(modbrick, image=detbrick.images[0], band=conf.DETECTION_NICKNAME)

        # Save segmap and blobmaps
        if (~is_borrowed) & (band_num == 0):
            tstart = time.time()
            logger.info('Saving segmentation and blob maps...')
            hdul = fits.HDUList()
            hdul.append(fits.PrimaryHDU())
            hdul.append(fits.ImageHDU(data=modbrick.segmap, name='SEGMAP'))
            hdul.append(fits.ImageHDU(data=modbrick.blobmap, name='BLOBMAP'))
            outpath = os.path.join(conf.INTERIM_DIR, f'B{brick_id}_SEGMAPS.fits')
            hdul.writeto(outpath, overwrite=conf.OVERWRITE)
            hdul.close()
            logger.info(f'Saved to {outpath} ({time.time() - tstart:3.3f}s)')

            tstart = time.time()
        else:
            logger.info(f'You gave me a catalog and segmap, so I am not saving it again.')

        if source_only:
            if source_id is None:
                raise ValueError('Source only is set True, but no source is has been provided!')
        
        # Run a specific source or blob
        if (source_id is not None) | (blob_id is not None):
            # conf.PLOT = True
            outcatalog = modbrick.catalog.copy()
            # print('AHHHHH   ', outcatalog['x', 'y'])
            mosaic_origin = modbrick.mosaic_origin
            # print('MOSAIC ORIGIN ', mosaic_origin)
            brick_id = modbrick.brick_id
            if source_id is not None:
                blob_id = np.unique(modbrick.blobmap[modbrick.segmap == source_id])
                if len(blob_id) == 1:
                    blob_id = blob_id[0]
                else:
                    raise ValueError('Requested source is not in brick!')
            if blob_id is not None:
                if blob_id not in outcatalog['blob_id']:
                    raise ValueError(f'No blobs exist for requested blob id {blob_id}')

            logger.info(f'Running single blob for blob {blob_id}')
            modblob = modbrick.make_blob(blob_id)

            # if source_id is set, then look at only that source



            
            if modblob.rejected:
                raise ValueError('Requested blob is invalid')
            if source_only & (source_id not in modblob.bcatalog['source_id']):
                logger.warning('Requested source is not in blob!')
                for source in modblob.bcatalog:
                    logger.warning(source['source_id'], source['cflux'])
                raise ValueError('Requested source is not in blob!')

            output_rows = runblob(blob_id, modblob, modeling=True, plotting=conf.PLOT, source_id=source_id)

            output_cat = vstack(output_rows)
                    
            for colname in output_cat.colnames:
                if colname not in outcatalog.colnames:
                    colshape = output_cat[colname].shape
                    if colname.startswith('FLUX_APER'):
                        outcatalog.add_column(Column(length=len(outcatalog), dtype=float, shape=(len(conf.APER_PHOT)), name=colname))
                    else:
                        outcatalog.add_column(Column(length=len(outcatalog), dtype=output_cat[colname].dtype, shape=(1,), name=colname))

            #outcatalog = join(outcatalog, output_cat, join_type='left', )
            for row in output_cat:
                outcatalog[np.where(outcatalog['source_id'] == row['source_id'])[0]] = row

            # vs = outcatalog['VALID_SOURCE']
            # scoords = SkyCoord(ra=outcatalog[vs]['RA'], dec=outcatalog[vs]['DEC'], unit='degree')
            # ebmv = m.ebv(scoords)
            # col_ebmv = Column(np.zeros_like(outcatalog['RA']), name='EBV')
            # col_ebmv[vs] = ebmv
            # outcatalog.add_column(col_ebmv)
            modbrick.catalog = outcatalog
        
        # Else, production mode -- all objects in brick are to be run.
        else:
            
            if conf.NBLOBS > 0:
                run_n_blobs = conf.NBLOBS
            else:
                run_n_blobs = modbrick.n_blobs
            logger.info(f'Preparing to run {run_n_blobs} blobs.')
            
            outcatalog = modbrick.catalog.copy()
            mosaic_origin = modbrick.mosaic_origin
            brick_id = modbrick.brick_id

            logger.info('Generating blobs...')
            astart = time.time()
            modblobs = (modbrick.make_blob(i) for i in np.arange(1, run_n_blobs+1))
            logger.info(f'{run_n_blobs} blobs generated ({time.time() - astart:3.3f}s)')
            #del modbrick

            tstart = time.time()

            if conf.NTHREADS > 0:

                with pa.pools.ProcessPool(ncpus=conf.NTHREADS) as pool:
                    logger.info(f'Parallel processing pool initalized with {conf.NTHREADS} threads.')
                    result = pool.uimap(partial(runblob, modeling=True, plotting=conf.PLOT, source_only=source_only), np.arange(1, run_n_blobs+1), modblobs)
                    output_rows = list(result)
                    logger.info('Parallel processing complete.')


            else:
                logger.info('Serial processing initalized.')
                output_rows = [runblob(kblob_id+1, kblob, modeling=True, plotting=conf.PLOT, source_only=source_only) for kblob_id, kblob in enumerate(modblobs)]

            output_cat = vstack(output_rows)

            ttotal = time.time() - tstart
            logger.info(f'Completed {run_n_blobs} blobs with {len(output_cat)} sources in {ttotal:3.3f}s (avg. {ttotal/len(output_cat):2.2f}s per source)')
                    
            for colname in output_cat.colnames:
                if colname not in outcatalog.colnames:
                    colshape = output_cat[colname].shape
                    if colname.startswith('FLUX_APER'):
                        outcatalog.add_column(Column(length=len(outcatalog), dtype=float, shape=(len(conf.APER_PHOT)), name=colname))
                    else:
                        outcatalog.add_column(Column(length=len(outcatalog), dtype=output_cat[colname].dtype, shape=(1,), name=colname))
            #outcatalog = join(outcatalog, output_cat, join_type='left', )
            for row in output_cat:
                outcatalog[np.where(outcatalog['source_id'] == row['source_id'])[0]] = row

            # vs = outcatalog['VALID_SOURCE']
            # scoords = SkyCoord(ra=outcatalog[vs]['RA'], dec=outcatalog[vs]['DEC'], unit='degree')
            # ebmv = m.ebv(scoords)
            # col_ebmv = Column(np.zeros_like(outcatalog['RA']), name='EBV')
            # col_ebmv[vs] = ebmv
            # outcatalog.add_column(col_ebmv)
            modbrick.catalog = outcatalog

            # open again and add

            # If user wants model and/or residual images made:
            if conf.MAKE_RESIDUAL_IMAGE:
                cleancatalog = outcatalog[outcatalog[f'VALID_SOURCE_{modbrick.bands[0]}']]
                modbrick.make_residual_image(catalog=cleancatalog)
            elif conf.MAKE_MODEL_IMAGE:
                cleancatalog = outcatalog[outcatalog[f'VALID_SOURCE_{modbrick.bands[0]}']]
                modbrick.make_model_image(catalog=cleancatalog)

        # Reconstuct mosaic positions of invalid sources 
        invalid = ~modbrick.catalog[f'VALID_SOURCE_{modbrick.bands[0]}']
        # modbrick.catalog[invalid][f'X_MODEL_{modbrick.bands[0]}'] = modbrick.catalog[invalid]['x_orig'] + modbrick.mosaic_origin[1] - conf.BRICK_BUFFER
        # modbrick.catalog[invalid][f'Y_MODEL_{modbrick.bands[0]}'] = modbrick.catalog[invalid]['y_orig'] + modbrick.mosaic_origin[0] - conf.BRICK_BUFFER

        # print(np.sum(invalid), len(invalid))
        # plt.pause(10)
        # idx = np.argwhere(invalid)[:20]
        # print(modbrick.catalog[idx][f'X_MODEL_{modbrick.bands[0]}'],  np.array(modbrick.catalog[idx]['x_orig']) + modbrick.mosaic_origin[1] - conf.BRICK_BUFFER)

    modbrick.catalog['x'] = modbrick.catalog['x'] + modbrick.mosaic_origin[1] - conf.BRICK_BUFFER
    modbrick.catalog['y'] = modbrick.catalog['y'] + modbrick.mosaic_origin[0] - conf.BRICK_BUFFER
    modbrick.catalog['x_orig'] = modbrick.catalog['x_orig'] + modbrick.mosaic_origin[1] - conf.BRICK_BUFFER
    modbrick.catalog['y_orig'] = modbrick.catalog['y_orig'] + modbrick.mosaic_origin[0] - conf.BRICK_BUFFER


    # If model bands is more than one, choose best one
    # Choose based on min chisq
    if len(img_names) > 1:
        logger.info(f'Selecting best-fit models within {len(img_names)} bands')
        name_arr = np.ones(shape=(len(modbrick.catalog), len(img_names)), dtype='U11')
        score_arr = np.zeros(shape=(len(modbrick.catalog), len(img_names)))
        valid_arr = np.zeros(shape=(len(modbrick.catalog), len(img_names)))
        xmodel_arr = np.zeros(shape=(len(modbrick.catalog), len(img_names)))
        ymodel_arr = np.zeros(shape=(len(modbrick.catalog), len(img_names)))
        for i, mod_band in enumerate(img_names):
            name_arr[:, i] = mod_band
            score_arr[:, i] = modbrick.catalog[f'CHISQ_{conf.MODELING_NICKNAME}_{mod_band}']
            xmodel_arr[:, i] = modbrick.catalog[f'X_MODEL_{conf.MODELING_NICKNAME}_{mod_band}']
            ymodel_arr[:, i] = modbrick.catalog[f'Y_MODEL_{conf.MODELING_NICKNAME}_{mod_band}']
            valid_arr[:, i] = modbrick.catalog[f'VALID_SOURCE_{conf.MODELING_NICKNAME}_{mod_band}']
            score_arr[np.logical_not(valid_arr[:,i]), i] = 1E31
        argmin_score = np.argmin(score_arr, 1)
        argmin_zero = np.min(score_arr, 1) == 1E31
        argmin_zero = np.zeros_like(argmin_zero)
        modbrick.catalog['BEST_MODEL_BAND'][~argmin_zero] = [modband_opt[k] for modband_opt, k in zip(name_arr[~argmin_zero], argmin_score[~argmin_zero])]
        modbrick.catalog['X_MODEL'][~argmin_zero] = [modband_opt[k] for modband_opt, k in zip(xmodel_arr[~argmin_zero], argmin_score[~argmin_zero])]
        modbrick.catalog['Y_MODEL'][~argmin_zero] = [modband_opt[k] for modband_opt, k in zip(ymodel_arr[~argmin_zero], argmin_score[~argmin_zero])]
        modbrick.catalog['VALID_SOURCE'][~argmin_zero] = [modband_opt[k] for modband_opt, k in zip(valid_arr[~argmin_zero], argmin_score[~argmin_zero])]
        if modbrick.wcs is not None:
                skyc = self.brick_wcs.all_pix2world(modbrick.catalog[f'X_MODEL'] - modbrick.mosaic_origin[0] + conf.BRICK_BUFFER, modbrick.catalog[f'Y_MODEL'] - modbrick.mosaic_origin[1] + conf.BRICK_BUFFER, 0)
                modbrick.bcatalog[row][f'RA'] = skyc[0]
                modbrick.bcatalog[row][f'DEC'] = skyc[1]
                logger.info(f"    Sky Model RA, Dec:   {skyc[0]:6.6f} deg, {skyc[1]:6.6f} deg")
    
    elif img_names[0] != conf.MODELING_NICKNAME:
        modbrick.catalog['BEST_MODEL_BAND'] = f'{conf.MODELING_NICKNAME}_{img_names[0]}'
        modbrick.catalog['X_MODEL'] = modbrick.catalog[f'X_MODEL_{conf.MODELING_NICKNAME}_{img_names[0]}']
        modbrick.catalog['Y_MODEL'] = modbrick.catalog[f'Y_MODEL_{conf.MODELING_NICKNAME}_{img_names[0]}']
        modbrick.catalog['VALID_SOURCE'] = modbrick.catalog[f'VALID_SOURCE_{conf.MODELING_NICKNAME}_{img_names[0]}']
    else:
        modbrick.catalog['BEST_MODEL_BAND'] = f'{conf.MODELING_NICKNAME}'
        modbrick.catalog['X_MODEL'] = modbrick.catalog[f'X_MODEL_{conf.MODELING_NICKNAME}']
        modbrick.catalog['Y_MODEL'] = modbrick.catalog[f'Y_MODEL_{conf.MODELING_NICKNAME}']
        modbrick.catalog['VALID_SOURCE'] = modbrick.catalog[f'VALID_SOURCE_{conf.MODELING_NICKNAME}']

    # write out cat
    if conf.OUTPUT:
        hdr = header_from_dict(conf.__dict__)
        hdu_info = fits.ImageHDU(header=hdr, name='CONFIG')
        hdu_table = fits.table_to_hdu(modbrick.catalog)
        hdul = fits.HDUList([fits.PrimaryHDU(), hdu_table, hdu_info])
        outpath = os.path.join(conf.CATALOG_DIR, f'B{brick_id}.cat')
        hdul.writeto(outpath, output_verify='ignore', overwrite=conf.OVERWRITE)
        logger.info(f'Wrote out catalog to {outpath}')


def force_models(brick_id, band=None, source_id=None, blob_id=None, insert=True, source_only=False):
    """ Stage 3. Force the models on the other images and solve only for flux. """

    # Create and update multiband brick
    tstart = time.time()

    if source_only:
            if source_id is None:
                raise ValueError('Source only is set True, but no source is has been provided!')

    if (source_id is None) & (blob_id is None):
        if (conf.NBLOBS == 0) & (conf.NTHREADS > 0) & (conf.PLOT > 0):
            conf.PLOT = 0
            logger.warning('Plotting not supported while forcing models in parallel!')

    if band is None:
        fband = conf.BANDS
    else:
        if (type(band) == list) | (type(band) == np.ndarray):
            fband = band
        elif (type(band) == str) | (type(band) == np.str_):
            fband = [band,]
        else:
            sys.exit('ERROR -- Input band is not a list, array, or string!')
            

    fbrick = stage_brickfiles(brick_id, nickname=conf.MULTIBAND_NICKNAME, band=fband, modeling=False)

    search_fn = os.path.join(conf.CATALOG_DIR, f'B{brick_id}.cat')
    if os.path.exists(search_fn):
        fbrick.catalog = Table(fits.open(search_fn)[1].data)
        fbrick.n_sources = len(fbrick.catalog)
        fbrick.n_blobs = fbrick.catalog['blob_id'].max()
    else:
        raise ValueError(f'No valid catalog was found for {brick_id}')
    search_fn = os.path.join(conf.INTERIM_DIR, f'B{brick_id}_SEGMAPS.fits')
    if os.path.exists(search_fn):
        hdul_seg = fits.open(search_fn)
        fbrick.segmap = hdul_seg['SEGMAP'].data
        fbrick.blobmap = hdul_seg['BLOBMAP'].data
    else:
        raise ValueError(f'No valid segmentation map was found for {brick_id}')

    fbrick.add_columns(modeling=False)

    logger.info(f'{conf.MULTIBAND_NICKNAME} brick #{brick_id} created ({time.time() - tstart:3.3f}s)')

    if conf.PLOT > 2:
        for plt_band in fband:
            if len(fband) == 1:
                idx = 0
            else:
                idx = np.argwhere(np.array(conf.BANDS)==plt_band)[0][0]
            plot_brick(fbrick, idx, band=plt_band)
            plot_background(fbrick, idx, band=plt_band)
            plot_mask(fbrick, idx, band=plt_band)

    for i, vb_band in enumerate(fband):
        logger.debug(f'Brick #{brick_id} -- Image statistics for {vb_band}')
        shape, minmax, mean, var = stats.describe(fbrick.images[i], axis=None)[:4]
        logger.debug(f'    Limits: {minmax[0]:3.3f} - {minmax[1]:3.3f}')
        logger.debug(f'    Mean: {mean:3.3f}+/-{np.sqrt(var):3.3f}\n')
        logger.debug(f'Brick #{brick_id} -- Weight statistics for {vb_band}')
        shape, minmax, mean, var = stats.describe(fbrick.weights[i], axis=None)[:4]
        logger.debug(f'    Limits: {minmax[0]:3.3f} - {minmax[1]:3.3f}')
        logger.debug(f'    Mean: {mean:3.3f}+/-{np.sqrt(var):3.3f}\n')
        logger.debug(f'Brick #{brick_id} -- Background statistics for {vb_band}')
        logger.debug(f'    Global: {fbrick.backgrounds[i, 0]:3.3f}')
        logger.debug(f'    RMS: {fbrick.backgrounds[i, 1]:3.3f}')
            

    logger.info(f'Forcing models on {len(fband)} {conf.MULTIBAND_NICKNAME} bands')

    tstart = time.time()
    if (source_id is not None) | (blob_id is not None):
        # conf.PLOT = True
        if source_id is not None:
            blob_id = np.unique(fbrick.blobmap[fbrick.segmap == source_id])
            assert(len(blob_id) == 1)
            blob_id = blob_id[0]
        fblob = fbrick.make_blob(blob_id)
        if source_only & (source_id not in fbrick.catalog['source_id']):
            print('Requested source is not in blob!')
            for source in fbrick.catalog:
                print(source['source_id'], source['cflux'])
            raise ValueError('Requested source is not in blob!')
        output_rows = runblob(blob_id, fblob, modeling=False, catalog=fbrick.catalog, plotting=conf.PLOT, source_id=source_id)

        output_cat = vstack(output_rows)
    
        if insert & conf.OVERWRITE & (conf.NBLOBS==0):
            # open old cat
            path_mastercat = os.path.join(conf.CATALOG_DIR, f'B{fbrick.brick_id}.cat')
            if os.path.exists(path_mastercat):
                mastercat = Table.read(path_mastercat, format='fits')

                # find new columns
                newcols = np.in1d(output_cat.colnames, mastercat.colnames, invert=True)
                # make fillers
                for colname in np.array(output_cat.colnames)[newcols]:
                    #mastercat.add_column(output_cat[colname])
                    if colname not in mastercat.colnames:
                        if colname.startswith('FLUX_APER'):
                            mastercat.add_column(Column(length=len(mastercat), dtype=float, shape=(len(conf.APER_PHOT),), name=colname))
                        else:
                            mastercat.add_column(Column(length=len(mastercat), dtype=output_cat[colname].dtype, shape=(1,), name=colname))

                for row in output_cat:
                    mastercat[np.where(mastercat['source_id'] == row['source_id'])[0]] = row
                # coordinate correction
                # fbrick.catalog['x'] = fbrick.catalog['x'] + fbrick.mosaic_origin[1] - conf.BRICK_BUFFER + 1.
                # fbrick.catalog['y'] = fbrick.catalog['y'] + fbrick.mosaic_origin[0] - conf.BRICK_BUFFER + 1.
                # save
                mastercat.write(os.path.join(conf.CATALOG_DIR, f'B{fbrick.brick_id}.cat'), format='fits', overwrite=conf.OVERWRITE)
                logger.info(f'Saving results for brick #{fbrick.brick_id} to existing catalog file.')
        else:
                
            for colname in output_cat.colnames:
                if colname not in fbrick.catalog.colnames:
                    
                    if colname.startswith('FLUX_APER'):
                        fbrick.catalog.add_column(Column(length=len(fbrick.catalog), dtype=float, shape=(len(conf.APER_PHOT),), name=colname))
                    else:
                        fbrick.catalog.add_column(Column(length=len(fbrick.catalog), dtype=output_cat[colname].dtype, shape=(1,), name=colname))

            #fbrick.catalog = join(fbrick.catalog, output_cat, join_type='left', )
            for row in output_cat:
                fbrick.catalog[np.where(fbrick.catalog['source_id'] == row['source_id'])[0]] = row

            mode_ext = conf.MULTIBAND_NICKNAME
            if fband is not None:
                if len(fband) == 1:
                    mode_ext = fband[0].replace(' ', '_')
                else:
                    mode_ext = conf.MULTIBAND_NICKNAME

            # write out cat
            # fbrick.catalog['x'] = fbrick.catalog['x'] + fbrick.mosaic_origin[1] - conf.BRICK_BUFFER + 1.
            # fbrick.catalog['y'] = fbrick.catalog['y'] + fbrick.mosaic_origin[0] - conf.BRICK_BUFFER + 1.
            if conf.OUTPUT:
                fbrick.catalog.write(os.path.join(conf.CATALOG_DIR, f'B{fbrick.brick_id}_{mode_ext}.cat'), format='fits', overwrite=conf.OVERWRITE)
                logger.info(f'Saving results for brick #{fbrick.brick_id} to new {mode_ext} catalog file.')


    else:

        if conf.NBLOBS > 0:
            run_n_blobs = conf.NBLOBS
        else:
            run_n_blobs = fbrick.n_blobs

        fblobs = (fbrick.make_blob(i) for i in np.arange(1, run_n_blobs+1))

        if conf.NTHREADS > 0:

            with pa.pools.ProcessPool(ncpus=conf.NTHREADS) as pool:
                logger.info(f'Parallel processing pool initalized with {conf.NTHREADS} threads.')
                result = pool.uimap(partial(runblob, modeling=False, catalog=fbrick.catalog, plotting=conf.PLOT), np.arange(1, run_n_blobs+1), fblobs)
                logger.info('Parallel processing complete.')
                output_rows = list(result)

        else:
            output_rows = [runblob(kblob_id, fbrick.make_blob(kblob_id), modeling=False, catalog=fbrick.catalog, plotting=conf.PLOT) for kblob_id in np.arange(1, run_n_blobs+1)]

        logger.info(f'Completed {run_n_blobs} blobs in {time.time() - tstart:3.3f}s')

        #output_rows = [x for x in output_rows if x is not None]

        output_cat = vstack(output_rows)

    
        if insert & conf.OVERWRITE & (conf.NBLOBS==0):
            # open old cat
            path_mastercat = os.path.join(conf.CATALOG_DIR, f'B{fbrick.brick_id}.cat')
            if os.path.exists(path_mastercat):
                mastercat = Table.read(path_mastercat, format='fits')

                # find new columns
                newcols = np.in1d(output_cat.colnames, mastercat.colnames, invert=True)
                # make fillers
                for colname in np.array(output_cat.colnames)[newcols]:
                    if colname not in mastercat.colnames:
                        if colname.startswith('FLUX_APER'):
                            mastercat.add_column(Column(length=len(mastercat), dtype=float, shape=(len(conf.APER_PHOT),), name=colname))
                        else:
                            mastercat.add_column(Column(length=len(mastercat), dtype=output_cat[colname].dtype, shape=(1,), name=colname))
                for row in output_cat:
                    mastercat[np.where(mastercat['source_id'] == row['source_id'])[0]] = row
                # coordinate correction
                # fbrick.catalog['x'] = fbrick.catalog['x'] + fbrick.mosaic_origin[1] - conf.BRICK_BUFFER + 1.
                # fbrick.catalog['y'] = fbrick.catalog['y'] + fbrick.mosaic_origin[0] - conf.BRICK_BUFFER + 1.
                # save
                hdr = header_from_dict(conf.__dict__)
                hdu_info = fits.ImageHDU(header=hdr, name='CONFIG')
                hdu_table = fits.table_to_hdu(mastercat)
                hdul = fits.HDUList([fits.PrimaryHDU(), hdu_table, hdu_info])
                hdul.writeto(os.path.join(conf.CATALOG_DIR, f'B{fbrick.brick_id}.cat'), overwrite=conf.OVERWRITE)
                logger.info(f'Saving results for brick #{fbrick.brick_id} to existing catalog file.')

                outcatalog = mastercat

        else:
                
            for colname in output_cat.colnames:
                if colname not in fbrick.catalog.colnames:
                    if colname.startswith('FLUX_APER'):
                        fbrick.catalog.add_column(Column(length=len(fbrick.catalog), dtype=float, shape=(len(conf.APER_PHOT),), name=colname))
                    else:
                        fbrick.catalog.add_column(Column(length=len(fbrick.catalog), dtype=output_cat[colname].dtype, shape=(1,), name=colname))
            #fbrick.catalog = join(fbrick.catalog, output_cat, join_type='left', )
            for row in output_cat:
                fbrick.catalog[np.where(fbrick.catalog['source_id'] == row['source_id'])[0]] = row

            mode_ext = conf.MULTIBAND_NICKNAME
            if fband is not None:
                if len(fband) == 1:
                    mode_ext = fband[0].replace(' ', '_')

            # write out cat
            mastercat = fbrick.catalog
            # fbrick.catalog['x'] = fbrick.catalog['x'] + fbrick.mosaic_origin[1] - conf.BRICK_BUFFER + 1.
            # fbrick.catalog['y'] = fbrick.catalog['y'] + fbrick.mosaic_origin[0] - conf.BRICK_BUFFER + 1.
            hdr = header_from_dict(conf.__dict__)
            hdu_info = fits.ImageHDU(header=hdr, name='CONFIG')
            hdu_table = fits.table_to_hdu(fbrick.catalog)
            hdul = fits.HDUList([fits.PrimaryHDU(), hdu_table, hdu_info])
            hdul.writeto(os.path.join(conf.CATALOG_DIR, f'B{fbrick.brick_id}_{mode_ext}.cat'), overwrite=conf.OVERWRITE)
            logger.info(f'Saving results for brick #{fbrick.brick_id} to new {mode_ext} catalog file.')

            outcatalog = fbrick.catalog


        # If user wants model and/or residual images made:

        if conf.MAKE_RESIDUAL_IMAGE:
            fbrick.make_residual_image(catalog=outcatalog)
        elif conf.MAKE_MODEL_IMAGE:
            fbrick.make_model_image(outcatalog)
        

    return 


def stage_brickfiles(brick_id, nickname='MISCBRICK', band=None, modeling=False):
    """ Essentially a private function. Pre-processes brick files and relevant catalogs """

    # Wraps Brick with a single parameter call
    # THIS ASSUMES YOU HAVE IMG, WGT, and MSK FOR ALL BANDS!

    path_brickfile = os.path.join(conf.BRICK_DIR, f'B{brick_id}_N{nickname}_W{conf.BRICK_WIDTH}_H{conf.BRICK_HEIGHT}.fits')

    if modeling & (band is None):
        sbands = [nickname,]
    elif band is None:
        sbands = conf.BANDS
    else:
        if type(band) == list:
            sbands = band
        else:
            sbands = [band,]
        # conf.BANDS = sbands

    if os.path.exists(path_brickfile):
        # Stage things
        images = np.zeros((len(sbands), conf.BRICK_WIDTH + 2 * conf.BRICK_BUFFER, conf.BRICK_HEIGHT + 2 * conf.BRICK_BUFFER))
        weights = np.zeros_like(images)
        masks = np.zeros_like(images, dtype=bool)

        # Loop over expected bands
        with fits.open(path_brickfile) as hdul_brick:

            # Attempt a WCS
            wcs = WCS(hdul_brick[1].header)

            # Stuff data into arrays
            for i, tband in enumerate(sbands):
                images[i] = hdul_brick[f"{tband}_IMAGE"].data
                weights[i] = hdul_brick[f"{tband}_WEIGHT"].data
                masks[i] = hdul_brick[f"{tband}_MASK"].data
    else:
        raise ValueError(f'Brick file not found for {path_brickfile}')

    psfmodels = np.zeros((len(sbands)), dtype=object)
    for i, band in enumerate(sbands):
        if band == conf.DETECTION_NICKNAME:
            continue

        if band in conf.PRFMAP_PSF:
            # read in prfmap table
            prftab = ascii.read(os.path.join(conf.PRFMAP_DIR, band+'_prfmap.dat'))
            prftab_ra = prftab[conf.PRFMAP_COLUMNS[1]]
            prftab_dec = prftab[conf.PRFMAP_COLUMNS[2]]
            prfcoords = SkyCoord(ra=prftab_ra*u.degree, dec=prftab_dec*u.degree)
            prfidx = prftab[conf.PRFMAP_COLUMNS[0]]
            psfmodels[i] = (prfcoords, prfidx)
            continue

        path_psffile = os.path.join(conf.PSF_DIR, f'{band}.psf')
        if os.path.exists(path_psffile) & (not conf.FORCE_GAUSSIAN_PSF):
            try:
                psfmodels[i] = PixelizedPsfEx(fn=path_psffile)
                logger.info(f'PSF model for {band} adopted as PixelizedPsfEx. ({path_psffile})')

            except:
                img = fits.open(path_psffile)[0].data
                img = img.astype('float32')
                img[img<=0.] = 1E-31
                psfmodels[i] = PixelizedPSF(img)
                logger.info(f'PSF model for {band} adopted as PixelizedPSF. ({path_psffile})')

        else:
            if conf.USE_GAUSSIAN_PSF:
                psfmodels[i] = None
                logger.warning(f'PSF model not found for {band} -- using {conf.PSF_SIGMA}" gaussian! ({path_psffile})')
            else:
                raise ValueError(f'PSF model not found for {band}! ({path_psffile})')

    if modeling:
        images, weights, masks = images[0], weights[0], masks[0]

    newbrick = Brick(images=images, weights=weights, masks=masks, psfmodels=psfmodels, wcs=wcs, bands=np.array(sbands), brick_id=brick_id)

    return newbrick


def models_from_catalog(catalog, fblob):
    """ Given an input catalog, construct models """
    # make multiband catalog from det output
    logger.info('Adopting sources from existing catalog.')
    model_catalog = -99 * np.ones(len(catalog), dtype=object)
    good_sources = np.ones(len(catalog), dtype=bool)

    band, rmvector = fblob.bands, fblob.mosaic_origin

    for i, src in enumerate(catalog):

        best_band = src['BEST_MODEL_BAND']

        if src['BEST_MODEL_BAND'] == '':
            good_sources[i] = False
            logger.warning(f'Source #{src["source_id"]}: no best-fit model chosen!')
            continue

        if (src[f'X_MODEL_{best_band}'] < 0) | (src[f'Y_MODEL_{best_band}'] < 0):
            good_sources[i] = False
            logger.warning(f'Source #{src["source_id"]}: {src[f"SOLMODEL_{best_band}"]} model at ({src[f"X_MODEL_{best_band}"]}, {src[f"Y_MODEL_{best_band}"]}) is INVALID.')
            continue

        position = [src[f'X_MODEL_{best_band}'], src[f'Y_MODEL_{best_band}']]

        position[0] -= (fblob.subvector[1] + fblob.mosaic_origin[1] - conf.BRICK_BUFFER)
        position[1] -= (fblob.subvector[0] + fblob.mosaic_origin[0] - conf.BRICK_BUFFER)

        position = PixPos(position[0], position[1])
        
        # src.pos[0] + self.subvector[1] + self.mosaic_origin[1] - conf.BRICK_BUFFER
        #     self.bcatalog[row][f'Y_MODEL_{mod_band}'] = src.pos[1] + self.subvector[0] + self.mosaic_origin[0] - conf.BRICK_BUFFER
        
        original_zpt = conf.MODELING_ZPT
        idx_bands = [fblob._band2idx(b) for b in fblob.bands]
        target_zpt = np.array(conf.MULTIBAND_ZPT)[idx_bands]
        flux_conv = src[f'RAWFLUX_{best_band}'] * 10 ** (-0.4 * (target_zpt - original_zpt))
        flux = Fluxes(**dict(zip(band, flux_conv)))

        # Check if valid source
        if not src[f'VALID_SOURCE_{best_band}']:
            good_sources[i] = False
            logger.warning(f'Source #{src["source_id"]}: {src[f"SOLMODEL_{best_band}"]} model at {position} is INVALID.')
            continue

        #shape = GalaxyShape(src['REFF'], 1./src['AB'], src['theta'])
        if src[f'SOLMODEL_{best_band}'] not in ('PointSource', 'SimpleGalaxy'):
            #shape = EllipseESoft.fromRAbPhi(src['REFF'], 1./src['AB'], -src['THETA'])  # Reff, b/a, phi
            shape = EllipseESoft(src[f'REFF_{best_band}'], src[f'EE1_{best_band}'], src[f'EE2_{best_band}'])

        if src[f'SOLMODEL_{best_band}'] == 'PointSource':
            model_catalog[i] = PointSource(position, flux)
            model_catalog[i].name = 'PointSource' # HACK to get around Dustin's HACK.
        elif src[f'SOLMODEL_{best_band}'] == 'SimpleGalaxy':
            model_catalog[i] = SimpleGalaxy(position, flux)
        elif src[f'SOLMODEL_{best_band}'] == 'ExpGalaxy':
            model_catalog[i] = ExpGalaxy(position, flux, shape)
        elif src[f'SOLMODEL_{best_band}'] == 'DevGalaxy':
            model_catalog[i] = DevGalaxy(position, flux, shape)
        elif src[f'SOLMODEL_{best_band}'] == 'FixedCompositeGalaxy':
            #expshape = EllipseESoft.fromRAbPhi(src['EXP_REFF'], 1./src['EXP_AB'],  -src['EXP_THETA'])
            #devshape = EllipseESoft.fromRAbPhi(src['DEV_REFF'], 1./src['DEV_AB'],  -src['DEV_THETA'])
            expshape = EllipseESoft(src[f'EXP_REFF_{best_band}'], src[f'EXP_EE1_{best_band}'], src[f'EXP_EE2_{best_band}'])
            devshape = EllipseESoft(src[f'DEV_REFF_{best_band}'], src[f'DEV_EE1_{best_band}'], src[f'DEV_EE2_{best_band}'])
            model_catalog[i] = FixedCompositeGalaxy(
                                            position, flux,
                                            SoftenedFracDev(src[f'FRACDEV_{best_band}']),
                                            expshape, devshape)

        logger.debug(f'Source #{src["source_id"]}: {src[f"SOLMODEL_{best_band}"]} model at {position}')
        logger.debug(f'               {flux}') 
        if src[f'SOLMODEL_{best_band}'] not in ('PointSource', 'SimpleGalaxy'):
            if src[f'SOLMODEL_{best_band}'] != 'FixedCompositeGalaxy':
                logger.debug(f'               {shape}')
            else:
                logger.debug(f'               {expshape}')
                logger.debug(f'               {devshape}')


    if (conf.FORCED_PHOT_MAX_NBLOB > 0) & (np.sum(good_sources) > conf.FORCED_PHOT_MAX_NBLOB):
        logger.warning(f'Number of good sources in blob ({np.sum(good_sources)}) exceeded limit of {conf.FORCED_PHOT_MAX_NBLOB}.')
        good_sources = np.zeros_like(good_sources, dtype=bool)

    return model_catalog[good_sources], good_sources
