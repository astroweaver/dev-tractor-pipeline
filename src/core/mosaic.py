# -*- coding: utf-8 -*-
"""

Authors
-------
John Weaver <john.weaver.astro@gmail.com>


About
-----
Class to handle mosaics (PSF + bricking)

Known Issues
------------
None


"""

# ------------------------------------------------------------------------------
# Standard Packages
# ------------------------------------------------------------------------------
import os
import sys
import numpy as np
import matplotlib.pyplot as plt
from astropy.io import ascii, fits
from astropy.table import Table, Column
from scipy.ndimage import label, binary_dilation, binary_fill_holes
from time import time
from astropy.wcs import WCS

from .subimage import Subimage
import config as conf
from .visualization import plot_ldac

import logging

class Mosaic(Subimage):
    
    def __init__(self, band, detection=False, modeling=False, psfmodel=None, wcs=None, header=None, mag_zeropoint=None, skip_build=False,
                ):

        self.logger = logging.getLogger('farmer.mosaic')

        if skip_build:
            self.logger.warning('Skipping mosaic build!')
        else:
            self.logger.info('Building mosaic...')

            if detection:
                self.path_image = os.path.join(conf.IMAGE_DIR, conf.DETECTION_FILENAME.replace('EXT', conf.IMAGE_EXT))
                self.path_weight = os.path.join(conf.IMAGE_DIR, conf.DETECTION_FILENAME.replace('EXT', conf.WEIGHT_EXT))
                self.path_mask = os.path.join(conf.IMAGE_DIR, conf.DETECTION_FILENAME.replace('EXT', conf.MASK_EXT))
                
            elif modeling & (band is conf.MODELING_NICKNAME):
                self.path_image = os.path.join(conf.IMAGE_DIR, conf.MODELING_FILENAME.replace('EXT', conf.IMAGE_EXT))
                self.path_weight = os.path.join(conf.IMAGE_DIR, conf.MODELING_FILENAME.replace('EXT', conf.WEIGHT_EXT))
                self.path_mask = os.path.join(conf.IMAGE_DIR, conf.MODELING_FILENAME.replace('EXT', conf.MASK_EXT))
            else:
                raw_band = np.array(conf.RAWBANDS)[np.array(conf.BANDS) == band][0] # this should be OK -- bands and rawbands are matched!
                self.path_image = os.path.join(conf.IMAGE_DIR, conf.MULTIBAND_FILENAME.replace('EXT', conf.IMAGE_EXT).replace('BAND', raw_band))
                self.path_weight = os.path.join(conf.IMAGE_DIR, conf.MULTIBAND_FILENAME.replace('EXT', conf.WEIGHT_EXT).replace('BAND', raw_band))
                self.path_mask = os.path.join(conf.IMAGE_DIR, conf.MULTIBAND_FILENAME.replace('EXT', conf.MASK_EXT).replace('BAND', raw_band))

            # open the files
            tstart = time()
            if os.path.exists(self.path_image):
                with fits.open(self.path_image, memmap=True) as hdu_image:
                    self.images = hdu_image['PRIMARY'].data
                    self.master_head = hdu_image['PRIMARY'].header
                    self.wcs = WCS(self.master_head)
                path = self.path_image
            else:
                raise ValueError(f'No image found at {self.path_image}')
            self.logger.info(f'Added image in {time()-tstart:3.3f}s. ({path})')

            tstart = time()
            if os.path.exists(self.path_weight):
                with fits.open(self.path_weight) as hdu_weight:
                    self.weights = hdu_weight['PRIMARY'].data
                path = self.path_weight
            else:
                #raise ValueError(f'No weight found at {self.path_weight}')
                self.weights = None
                path = 'None found. Assuming equal weights.'
            self.logger.info(f'Added weight in {time()-tstart:3.3f}s. ({path})')


            tstart = time()
            if os.path.exists(self.path_mask):
                with fits.open(self.path_mask) as hdu_mask:
                    self.masks = hdu_mask['PRIMARY'].data
                path = self.path_mask
            else:
                self.masks = None    
                path = 'None found. Assuming no masking.'
            self.logger.info(f'Added mask in {time()-tstart:3.3f}s. ({path})')

        self.psfmodels = psfmodel
        self.bands = band
        self.mag_zeropoints = mag_zeropoint
        
        super().__init__()

    def _make_psf(self, xlims, ylims, override=False, sextractor_only=False, psfex_only=False):

        # Set filenames
        psf_dir = conf.PSF_DIR
        unclean_cat = os.path.join(conf.PSF_DIR, f'{self.bands}.ldac')
        psf_cat = os.path.join(conf.PSF_DIR, f'{self.bands}_clean.ldac')
        path_savexml = conf.PSF_DIR
        path_savechkimg = ','.join([os.path.join(conf.PSF_DIR, ext) for ext in ('chi', 'proto', 'samp', 'resi', 'snap')])
        path_savechkplt = ','.join([os.path.join(conf.PSF_DIR, ext) for ext in ('fwhm', 'ellipticity', 'counts', 'countfrac', 'chi2', 'resi')])

        # if forced_psf:
        #     self.path_image = os.path.join(conf.IMAGE_DIR, conf.DETECTION_FILENAME.replace('EXT', conf.IMAGE_EXT)) + f',{self.path_image}'

        # run SEXTRACTOR in LDAC mode
        if (not os.path.exists(psf_cat)) | override:
            
            if not psfex_only:
                try:
                    #os.system('sextractor {} -c config/config_psfex.sex -PARAMETERS_NAME config/param_psfex.sex -CATALOG_NAME {} -CATALOG_TYPE FITS_LDAC -WEIGHT_TYPE MAP_WEIGHT -WEIGHT_IMAGE {} -MAG_ZEROPOINT {}'.format(path_im, path_outcat, path_wt, zpt))
                    # print(f'sex {self.path_image} -c config/config_psfex.sex -PARAMETERS_NAME config/param_psfex.sex -CATALOG_NAME {psf_cat} -CATALOG_TYPE FITS_LDAC -CHECKIMAGE_TYPE SEGMENTATION -CHECKIMAGE_NAME {path_segmap} -MAG_ZEROPOINT {self.mag_zeropoints}')
                    os.system(f'sex {self.path_image} -c config/config_psfex.sex -PARAMETERS_NAME config/param_psfex.sex -CATALOG_NAME {unclean_cat} -CATALOG_TYPE FITS_LDAC -MAG_ZEROPOINT {self.mag_zeropoints} ') #-BACK_TYPE MANUAL -BACK_VALUE 0.00001')
                    # #print('RUNNING SEXTRACTOR WITHOUT SEGMAP')
                    # #os.system(f'sex {self.path_image} -c config/config_psfex.sex -PARAMETERS_NAME config/param_psfex.sex -CATALOG_NAME {psf_cat} -CATALOG_TYPE FITS_LDAC -MAG_ZEROPOINT {self.mag_zeropoints}')
                    # sys.exit()
                    self.logger.info('SExtractor succeded!')
                except:
                    raise ValueError('SExtractor failed!')

            self.logger.debug(f'LDAC crop parameters: {xlims}, {ylims}')

            hdul_ldac = fits.open(unclean_cat, ignore_missing_end=True, mode='update')
            tab_ldac = hdul_ldac['LDAC_OBJECTS'].data

            n_obj = len(tab_ldac)
            self.logger.debug(f'{n_obj} sources found.')

            if conf.PLOT > 0:
                self.logger.debug('Plotting LDAC without pointsource bounding box')
                plot_ldac(tab_ldac, self.bands, box=False)
                

            mask_ldac = (tab_ldac['MAG_AUTO'] > ylims[0]) &\
                    (tab_ldac['MAG_AUTO'] < ylims[1]) &\
                    (tab_ldac['FLUX_RADIUS'] > xlims[0]) &\
                    (tab_ldac['FLUX_RADIUS'] < xlims[1])

            n_obj = np.sum(mask_ldac)
            if n_obj == 0:
                raise ValueError('No sources selected.')

            self.logger.info(f'Found {n_obj} objects to determine PSF')

            if conf.PLOT > 0:
                self.logger.debug('Plotting LDAC with pointsource bounding box')
                plot_ldac(tab_ldac, self.bands, xlims=xlims, ylims=ylims, box=True, nsel=np.sum(mask_ldac))

            hdul_ldac['LDAC_OBJECTS'].data = tab_ldac[mask_ldac]
            hdul_ldac.writeto(psf_cat, overwrite=override)

            # RUN PSF
            if not sextractor_only:
                psfvar_nsnap = 1
                if self.bands not in conf.CONSTANT_PSF:
                    psfvar_nsnap = conf.PSFVAR_NSNAP
                    self.logger.info(f'Creating spatially-varying PSF with PSFNSNAP = {psfvar_nsnap}')
                else:
                    self.logger.info(f'Creating constant PSF')

                cmd = f'psfex {psf_cat} -c config/config.psfex -BASIS_TYPE PIXEL -PSF_DIR {psf_dir} -PSFVAR_NSNAP {psfvar_nsnap} -WRITE_XML Y -XML_NAME {path_savexml} -CHECKIMAGE_NAME {path_savechkimg} -CHECKPLOT_NAME {path_savechkplt}'
                self.logger.debug(cmd)
                os.system(cmd)
                try:
                    oldpath = os.path.join(psf_dir, self.bands+"_clean.psf")
                    newpath = os.path.join(psf_dir, self.bands+".psf")
                    self.logger.debug(f'Moving {oldpath} to {newpath}')
                    os.system(f'mv {oldpath} {newpath}')
                except:
                    self.logger.warning(f'Could not move {oldpath} to {newpath} ')
        else:
            self.logger.critical('No PSF attempted. PSF LDAC already exists and override is off')
        
    def _make_brick(self, brick_id, overwrite=False, detection=False, modeling=False,
            brick_width=conf.BRICK_WIDTH, brick_height=conf.BRICK_HEIGHT, brick_buffer=conf.BRICK_BUFFER):

        self.logger.info(f'Making brick {brick_id}/{self.n_bricks()}')

        if detection:
            nickname = conf.DETECTION_NICKNAME
        elif modeling:
            nickname = conf.MODELING_NICKNAME
        else:
            nickname = conf.MULTIBAND_NICKNAME

        save_fitsname = f'B{brick_id}_N{nickname}_W{brick_width}_H{brick_height}.fits'
        path_fitsname = os.path.join(conf.BRICK_DIR, save_fitsname)

        if (not overwrite) & (not os.path.exists(path_fitsname)):
            raise ValueError(f'No existing file found for {path_fitsname}. Will not write new one.')

        x0, y0 = self._get_origin(brick_id, brick_width, brick_height)
        if (brick_width + 2*brick_buffer == conf.MOSAIC_WIDTH) & (brick_height + 2*brick_buffer == conf.MOSAIC_HEIGHT):
            x0, y0 = brick_buffer, brick_buffer
        subinfo = self._get_subimage(x0, y0, brick_width, brick_height, brick_buffer)
        subimage, subweight, submask, psfmodel, band, subwcs, subvector, slicepix, subslice = subinfo

        if detection | modeling:
            sbands = nickname
        else:
            sbands = self.bands

        # Remove n_bands = 1 dimension
        subimage, subweight, submask = subimage[0], subweight[0], submask[0]
        
        # Make hdus
        head_image = self.master_head.copy()
        head_image.update(subwcs.to_header())
        hdu_image = fits.ImageHDU(subimage, head_image, f'{sbands}_IMAGE')
        hdu_weight = fits.ImageHDU(subweight, head_image, f'{sbands}_WEIGHT')
        hdu_mask = fits.ImageHDU(submask.astype(int), head_image, f'{sbands}_MASK')
        
        # if overwrite, make it
        if overwrite:
            hdu_prim = fits.PrimaryHDU()
            hdul_new = fits.HDUList([hdu_prim, hdu_image, hdu_weight, hdu_mask])
            hdul_new.writeto(path_fitsname, overwrite=True)
        else:
        # otherwise add to it
            exist_hdul = fits.open(path_fitsname, mode='append')
            exist_hdul.append(hdu_image)
            exist_hdul.append(hdu_weight)
            exist_hdul.append(hdu_mask)
            exist_hdul.flush()
            exist_hdul.close()

    def _get_origin(self, brick_id, brick_width=conf.BRICK_WIDTH, brick_height=conf.BRICK_HEIGHT):
        x0 = int(((brick_id - 1) * brick_width) % self.dims[0])
        y0 = int(((brick_id - 1) * brick_height) / self.dims[1]) * brick_height
        return np.array([x0, y0])

    def n_bricks(self, brick_width=conf.BRICK_WIDTH, brick_height=conf.BRICK_HEIGHT):
        n_xbricks = self.dims[0] / brick_width
        n_ybricks = self.dims[1] / brick_height
        return int(n_xbricks * n_ybricks)



