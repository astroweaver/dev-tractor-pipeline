# -*- coding: utf-8 -*-
"""

Authors
-------
John Weaver <john.weaver.astro@gmail.com>


About
-----
Class to handle mosaic subimages (i.e. bricks)

Known Issues
------------
None


"""

import os
import sys
import numpy as np

from astropy.table import Column
from astropy.io import fits
from scipy.ndimage import label, binary_dilation, binary_fill_holes

from tractor import NCircularGaussianPSF, PixelizedPSF, Image, Tractor, FluxesPhotoCal, NullWCS, ConstantSky, EllipseESoft, Fluxes, PixPos
from tractor.galaxy import ExpGalaxy, DevGalaxy, FixedCompositeGalaxy, SoftenedFracDev
from tractor.pointsource import PointSource

from .utils import create_circular_mask, SimpleGalaxy
from .visualization import plot_blobmap, plot_detblob, plot_fblob
from .subimage import Subimage
from .blob import Blob
import config as conf

import logging

class Brick(Subimage):
    """TODO: docstring"""

    def __init__(self,
                 images,
                 weights=None,
                 masks=None,
                 psfmodels=None,
                 wcs=None,
                 bands=None,
                 buffer=conf.BRICK_BUFFER,
                 brick_id=-99):
        """TODO: docstring"""

        self.logger = logging.getLogger('farmer.brick')

        self.wcs = wcs
        self.images = images
        self.weights = weights
        self.masks = masks
        self.psfmodels = psfmodels
        self.bands = np.array(bands)


        super().__init__()

        self._buffer = buffer
        self.brick_id = brick_id

        self.segmap = None
        self.blobmap = None

        self._buff_left = self._buffer
        self._buff_right = self.dims[0] - self._buffer
        self._buff_bottom = self._buffer
        self._buff_top = self.dims[1] - self._buffer

        # Replace mask
        # self._masks[:, :, :self._buff_left] = True
        # self._masks[:, :, self._buff_right:] = True
        # self._masks[:, :self._buff_bottom] = True
        # self._masks[:, self._buff_top:] = True

        x0 = int(((brick_id - 1) * conf.BRICK_WIDTH) % conf.MOSAIC_WIDTH)
        y0 = int(((brick_id - 1) * conf.BRICK_HEIGHT) / conf.MOSAIC_HEIGHT) * conf.BRICK_HEIGHT
        self.mosaic_origin = np.array([x0, y0])

        self.model_images = None
        self.residual_images = None
        self.nopsf_images = None
        self.auxhdu_path = os.path.join(conf.INTERIM_DIR, f'B{brick_id}_AUXILLARY_MAPS.fits')

    @property
    def buffer(self):
        return self._buffer

    def cleanup(self):
        """TODO: docstring"""
        self.clean_segmap()

        self.clean_catalog()

        self.dilate()

        self.relabel()

        self.add_ids()

    def clean_segmap(self):
        """TODO: docstring"""
        coords = np.array([self.catalog['x'], self.catalog['y']]).T
        self._allowed_sources = (coords[:,0] > self._buff_left) & (coords[:,0] < self._buff_right )\
                        & (coords[:,1] > self._buff_bottom) & (coords[:,1] < self._buff_top)
        
        idx = np.where(~self._allowed_sources)[0]
        for i in idx:
            self.segmap[self.segmap == i+1] = 0

    

    def clean_catalog(self):
        """TODO: docstring"""
        sid_col = np.arange(1, self.n_sources+1, dtype=int)
        self.catalog.add_column(Column(sid_col.astype(int), name='source_id'), 0)
        self.catalog = self.catalog[self._allowed_sources]
        self.n_sources = len(self.catalog)

    def add_columns(self, modeling=True, modbrick_name=conf.MODELING_NICKNAME):
        """TODO: docstring"""
        filler = np.zeros(len(self.catalog))
        if 'N_BLOB' not in self.catalog.colnames:
            self.catalog.add_column(Column(-99*np.ones(len(self.catalog), dtype=int), name='N_BLOB'))
        if 'BEST_MODEL_BAND' not in self.catalog.colnames:
            self.catalog.add_column(Column(np.zeros(len(self.catalog), dtype='S20'), name='BEST_MODEL_BAND'))
        if 'X_MODEL' not in self.catalog.colnames:
            self.catalog.add_column(Column(-99*np.ones(len(self.catalog), dtype=float), name='X_MODEL'))
        if 'Y_MODEL' not in self.catalog.colnames:
            self.catalog.add_column(Column(-99*np.ones(len(self.catalog), dtype=float), name='Y_MODEL'))
        if 'RA' not in self.catalog.colnames:
            self.catalog.add_column(Column(-99*np.ones(len(self.catalog), dtype=float), name='RA'))
        if 'DEC' not in self.catalog.colnames:
            self.catalog.add_column(Column(-99*np.ones(len(self.catalog), dtype=float), name='DEC'))
        if 'VALID_SOURCE' not in self.catalog.colnames:
            self.catalog.add_column(Column(np.zeros(len(self.catalog), dtype=bool), name='VALID_SOURCE'))
        for colname in self.bands:
            colname = colname.replace(' ', '_')
            self.logger.debug(f'Adding columns to catalog for {colname}')
            try:
                self.catalog.add_column(Column(filler, name=f'MAG_{colname}'))
                self.catalog.add_column(Column(filler, name=f'MAGERR_{colname}'))
                self.catalog.add_column(Column(filler, name=f'RAWFLUX_{colname}'))
                self.catalog.add_column(Column(filler, name=f'RAWFLUXERR_{colname}'))
                self.catalog.add_column(Column(filler, name=f'FLUX_{colname}'))
                self.catalog.add_column(Column(filler, name=f'FLUXERR_{colname}'))
                self.catalog.add_column(Column(filler, name=f'CHISQ_{colname}'))
                self.catalog.add_column(Column(filler, name=f'BIC_{colname}'))
                self.catalog.add_column(Column(filler, name=f'N_CONVERGE_{colname}'))
            except:
                self.logger.debug(f'Columns already exist for {colname}')
            if modeling:
                try:
                    self.logger.debug(f'Adding model columns to catalog.')
                    for colname_fill in [f'{colext}_{colname}' for colext in ('X_MODEL', 'Y_MODEL', 'XERR_MODEL', 'YERR_MODEL', 'RA', 'DEC')]:
                        self.catalog.add_column(Column(filler, name=colname_fill))
                    self.catalog.add_column(Column(np.zeros(len(self.catalog), dtype='S20'), name=f'SOLMODEL_{colname}'))
                    self.catalog.add_column(Column(np.zeros(len(self.catalog), dtype=bool), name=f'VALID_SOURCE_{colname}'))
                    
                    for colname_fill in [f'{colext}_{colname}' for colext in ('REFF', 'REFF_ERR', 'EE1', 'EE2', 'AB', 'AB_ERR', 'THETA', 'THETA_ERR',
                                'FRACDEV', 'EXP_REFF', 'EXP_REFF_ERR', 'EXP_EE1', 'EXP_EE2', 'EXP_AB', 'EXP_AB_ERR', 'EXP_THETA', 'EXP_THETA_ERR', 
                                'DEV_REFF', 'DEV_REFF_ERR', 'DEV_EE1', 'DEV_EE2', 'DEV_AB', 'DEV_AB_ERR', 'DEV_THETA', 'DEV_THETA_ERR' )]:
                        self.catalog.add_column(Column(filler, name=colname_fill))
                except:
                    self.logger.debug(f'Model columns already exist')

    def dilate(self, radius=conf.DILATION_RADIUS, fill_holes=True):
        """TODO: docstring"""
        # Make binary
        segmask = self.segmap.copy()  # I don't see how we can get around this.
        segmask[segmask != 0] = 1
        # Dilate
        struct2 = create_circular_mask(2*radius, 2*radius, radius=radius)
        segmask = binary_dilation(segmask, structure=struct2)

        if fill_holes:
            segmask = binary_fill_holes(segmask).astype(int)

        self.segmask = segmask
        return segmask

    def relabel(self):
        self.blobmap, self.n_blobs = label(self.segmask)

        return self.blobmap, self.n_blobs

    def add_ids(self):
        """TODO: docstring. rename sid and bid throughout"""
        brick_col = float(self.brick_id) * np.ones(self.n_sources, dtype=int)
        self.catalog.add_column(Column(brick_col.astype(int), name='brick_id'), 1)

        blob_col = np.array([np.unique(self.blobmap[self.segmap == sid])[0] for sid in self.catalog['source_id']])
        self.catalog.add_column(Column(blob_col.astype(int), name='blob_id'), 1)

    def make_blob(self, blob_id):

        if blob_id < 1:
            raise ValueError('Blob id must be greater than 0.')

        blob = Blob(self, blob_id)

        return blob

    def make_model_image(self, catalog, include_chi=True, include_nopsf=False, save=True):

        # Make Images
        self.logger.info('Making a model image...')
        self.logger.debug('Creating blank image.')

        timages = np.zeros(self.n_bands, dtype=object)

        for i, (psf, band) in enumerate(zip(self.psfmodels, self.bands)):

            remove_background_psf = False
            if band in conf.RMBACK_PSF:
                remove_background_psf = True

            if (band in conf.CONSTANT_PSF) & (psf is not None):
                psfmodel = psf.constantPsfAt(conf.MOSAIC_WIDTH/2., conf.MOSAIC_HEIGHT/2.)
                if remove_background_psf & (not conf.FORCE_GAUSSIAN_PSF):
                    pw, ph = np.shape(psfmodel.img)
                    cmask = create_circular_mask(pw, ph, radius=conf.PSF_MASKRAD / conf.PIXEL_SCALE)
                    bcmask = ~cmask.astype(bool) & (psfmodel.img > 0)
                    # psfmodel.img -= np.nanmedian(psfmodel.img[bcmask])
                    # psfmodel.img[np.isnan(psfmodel.img)] = 0
                    psfmodel.img -= np.nanmax(psfmodel.img[bcmask])
                    psfmodel.img[(psfmodel.img < 0) | np.isnan(psfmodel.img)] = 0
                if conf.NORMALIZE_PSF & (not conf.FORCE_GAUSSIAN_PSF):
                    psfmodel.img /= psfmodel.img.sum() # HACK -- force normalization to 1
                self.logger.debug(f'blob.stage_images :: Adopting constant PSF.')

                if conf.PLOT > 1:
                    import matplotlib.pyplot as plt
                    fig, ax = plt.subplots()
                    ax.imshow(psf.getImage(conf.MOSAIC_WIDTH/2., conf.MOSAIC_HEIGHT/2.))
                    fig.savefig(os.path.join(conf.PLOT_DIR, f'{band}_psf.pdf'))

            elif (psf is not None):
                # blobmask = np.array(self.blobmap == src['blob_id'], bool)
                # idx, idy = blobmask.nonzero()
                # xlo, xhi = np.min(idx), np.max(idx) + 1
                # ylo, yhi = np.min(idy), np.max(idy) + 1
                # w = xhi - xlo
                # h = yhi - ylo

                # left = x0 - conf.BLOB_BUFFER
                # bottom = y0 - conf.BLOB_BUFFER

                # subvector = (left, bottom)

                # blob_center = (xlo + w/2., ylo + h/2.)
                # blob_centerx = blob_center[0] + self.subvector[1] + self.mosaic_origin[1] - conf.BRICK_BUFFER + 1
                # blob_centery = blob_center[1] + self.subvector[0] + self.mosaic_origin[0] - conf.BRICK_BUFFER + 1
                blob_centerx = self.mosaic_origin[1] - conf.BRICK_BUFFER + 1
                blob_centery = self.mosaic_origin[0] - conf.BRICK_BUFFER + 1
                psfmodel = psf.constantPsfAt(blob_centerx, blob_centery) # init at brick center, for now...
                if remove_background_psf & (not conf.FORCE_GAUSSIAN_PSF):
                    pw, ph = np.shape(psfmodel.img)
                    cmask = create_circular_mask(pw, ph, radius=conf.PSF_MASKRAD / conf.PIXEL_SCALE)
                    bcmask = ~cmask.astype(bool) & (psfmodel.img > 0)
                    # psfmodel.img -= np.nanmax(psfmodel.img[bcmask])
                    # psfmodel.img[np.isnan(psfmodel.img)] = 0
                    psfmodel.img -= np.nanmax(psfmodel.img[bcmask])
                    psfmodel.img[(psfmodel.img < 0) | np.isnan(psfmodel.img)] = 0
                if conf.NORMALIZE_PSF & (not conf.FORCE_GAUSSIAN_PSF):
                    psfmodel.img /= psfmodel.img.sum() # HACK -- force normalization to 1
                self.logger.debug(f'blob.stage_images :: Adopting varying PSF constant at ({blob_centerx}, {blob_centery})')

            elif (psf is None):
                if conf.USE_GAUSSIAN_PSF:
                    psfmodel = NCircularGaussianPSF([conf.PSF_SIGMA / conf.PIXEL_SCALE], [1,])
                    self.logger.debug(f'Adopting {conf.PSF_SIGMA}" Gaussian PSF.')
                else:
                    raise ValueError(f'WARNING - No PSF model found for {band}!')

            timages[i] = Image(data=np.zeros_like(self.images[0]),
                            invvar=np.ones_like(self.images[0]),
                            psf=psfmodel,
                            wcs=NullWCS(),
                            photocal=FluxesPhotoCal(band),
                            sky=ConstantSky(0.),
                            name=band)

        self.timages = timages

        # Make models
        self.model_catalog = np.zeros(len(catalog), dtype=object)
        self.model_mask = np.ones(len(catalog), dtype=bool)
        for i, src in enumerate(catalog):
            
            if (catalog['BEST_MODEL_BAND'] == '').all():
                self.logger.debug('No best models chosen yet.')
                best_band = self.bands[0]
                if not src[f'VALID_SOURCE_{self.bands[0]}']:
                    self.model_mask[i] = False
                    self.logger.debug('Source does not have a valid model.')
                    continue
            else:
                best_band = src['BEST_MODEL_BAND']

                if not src[f'VALID_SOURCE_{best_band}']:
                    self.model_mask[i] = False
                    continue

            raw_fluxes = np.array([src[f'RAWFLUX_{band}'] for band in self.bands])
            if conf.RESIDUAL_CHISQ_REJECTION is not None:
                for j, band in enumerate(self.bands):
                    chisq_band = src[f'CHISQ_{band}']
                    if chisq_band > conf.RESIDUAL_CHISQ_REJECTION:
                        raw_fluxes[j] = 0.0
                        self.logger.debug(f'Source has too large chisq in {band}. ({chisq_band:3.3f}) > {conf.RESIDUAL_CHISQ_REJECTION})')
                if (raw_fluxes <= 0.0).all():
                    self.model_mask[i] = False
                    self.logger.debug('Source has too large chisq in all bands. Rejecting!')
            if conf.RESIDUAL_NEGFLUX_REJECTION:
                if (raw_fluxes <= 0.0).all():
                    self.model_mask[i] = False
                    self.logger.debug('Source has negative flux in all bands. Rejecting!')
                elif (raw_fluxes < 0.0).any():
                    raw_fluxes[raw_fluxes < 0.0] = 0.0
                    self.logger.debug('Source has negative flux in some bands.')
                    

            # self.bcatalog[row]['X_MODEL'] = src.pos[0] + self.subvector[1] + self.mosaic_origin[1] - conf.BRICK_BUFFER + 1
            # self.bcatalog[row]['Y_MODEL'] = src.pos[1] + self.subvector[0] + self.mosaic_origin[0] - conf.BRICK_BUFFER + 1

            bx_model = src[f'X_MODEL_{best_band}'] - self.mosaic_origin[1] + conf.BRICK_BUFFER - 1
            by_model = src[f'Y_MODEL_{best_band}'] - self.mosaic_origin[0] + conf.BRICK_BUFFER - 1

            position = PixPos(bx_model, by_model)
            flux = Fluxes(**dict(zip(self.bands, raw_fluxes))) # IMAGES ARE IN NATIVE ZPT, USE RAWFLUXES!

            if src[f'SOLMODEL_{best_band}'] == "PointSource":
                self.model_catalog[i] = PointSource(position, flux)
                self.model_catalog[i].name = 'PointSource' # HACK to get around Dustin's HACK.
            elif src[f'SOLMODEL_{best_band}'] == "SimpleGalaxy":
                self.model_catalog[i] = SimpleGalaxy(position, flux)
            elif src[f'SOLMODEL_{best_band}'] == "ExpGalaxy":
                shape = EllipseESoft(src[f'REFF_{best_band}'], src[f'EE1_{best_band}'], src[f'EE2_{best_band}'])
                self.model_catalog[i] = ExpGalaxy(position, flux, shape)
            elif src[f'SOLMODEL_{best_band}'] == "DevGalaxy":
                shape = EllipseESoft(src[f'REFF_{best_band}'], src[f'EE1_{best_band}'], src[f'EE2_{best_band}'])
                self.model_catalog[i] = DevGalaxy(position, flux, shape)
            elif src[f'SOLMODEL_{best_band}'] == "FixedCompositeGalaxy":
                shape_exp = EllipseESoft(src[f'EXP_REFF_{best_band}'], src[f'EXP_EE1_{best_band}'], src[f'EXP_EE2_{best_band}'])
                shape_dev = EllipseESoft(src[f'DEV_REFF_{best_band}'], src[f'DEV_EE1_{best_band}'], src[f'DEV_EE2_{best_band}'])
                self.model_catalog[i] = FixedCompositeGalaxy(
                                                position, flux,
                                                SoftenedFracDev(src[f'FRACDEV_{best_band}']),
                                                shape_exp, shape_dev)
            else:
                self.logger.warning(f'Source #{src["source_id"]}: has no solution model at {position}')
                self.model_mask[i] = False
                continue

            self.logger.debug(f'Source #{src["source_id"]}: {self.model_catalog[i].name} model at {position}')
            self.logger.debug(f'               {flux}') 

        # Clean
        mtotal = len(self.model_catalog)
        nmasked = np.sum(~self.model_mask)
        msrc = np.sum(self.model_mask)
        if not self.model_mask.any():        
            raise RuntimeError(f'No valid models to make model image! (of {mtotal}, {nmasked} masked)')
        self.logger.info(f'Making model image with {msrc}/{mtotal} sources. ({nmasked} are masked)')
        self.model_catalog = self.model_catalog[self.model_mask]


        # Tractorize
        tr = Tractor(self.timages, self.model_catalog)
        self.tr = tr

        self.logger.info(f'Computing model image...')
        self.model_images = [tr.getModelImage(k) for k in np.arange(self.n_bands)]
        if include_chi:
            self.logger.info(f'Computing chi image...')
            self.chisq_images = [tr.getChiImage(k) for k in np.arange(self.n_bands)]

        # If no psf:
        if include_nopsf:
            self.logger.warning('INCLUSION OF NO PSF IS CURRENTLY DISABLED.')
            include_nopsf = False
            # tr_nopsf = tr.copy()
            # [tr_nopsf.images[k].setPsf(None) for k in np.arange(self.n_bands)]
            # self.logger.info(f'Computing model image without PSF...')
            # self.nopsf_images = [tr_nopsf.getModelImage(k) for k in np.arange(self.n_bands)]

        if save:
            if os.path.exists(self.auxhdu_path):
                self.logger.info(f'Saving image(s) to existing file, {self.auxhdu_path}')
                # Save to file
                hdul = fits.open(self.auxhdu_path, mode='update')
                for i, band in enumerate(self.bands):
                    hdu_img = fits.ImageHDU(data=self.images[i], name=f'{band}_IMAGE')
                    hdul.append(hdu_img)
                    hdu_mod = fits.ImageHDU(data=self.model_images[i], name=f'{band}_MODEL')
                    hdul.append(hdu_mod)
                    if include_chi:
                        hdu_chi = fits.ImageHDU(data=self.chisq_images[i], name=f'{band}_CHI')
                        hdul.append(hdu_chi)
                    if include_nopsf:
                        hdu_nopsf = fits.ImageHDU(data=self.nopsf_images[i], name=f'{band}_NOPSF')
                        hdul.append(hdu_nopsf)

                hdul.flush()

            else:
                self.logger.info(f'Saving image(s) to new file, {self.auxhdu_path}')
                # Save to file
                hdul = fits.HDUList()
                for i, band in enumerate(self.bands):
                    hdu_img = fits.ImageHDU(data=self.images[i], name=f'{band}_IMAGE')
                    hdul.append(hdu_img)
                    hdu_mod = fits.ImageHDU(data=self.model_images[i], name=f'{band}_MODEL')
                    hdul.append(hdu_mod)
                    if include_chi:
                        hdu_chi = fits.ImageHDU(data=self.chisq_images[i], name=f'{band}_CHI')
                        hdul.append(hdu_chi)
                    if include_nopsf:
                        hdu_nopsf = fits.ImageHDU(data=self.nopsf_images[i], name=f'{band}_NOPSF')
                        hdul.append(hdu_nopsf)

                hdul.writeto(self.auxhdu_path, overwrite=True)


    def make_residual_image(self, catalog=None, include_chi=True, include_nopsf=False, save=True):
        # Make model image or load it in
        self.logger.info(f'Making residual image')

        if include_nopsf:
            self.logger.warning('INCLUSION OF NO PSF IS CURRENTLY DISABLED.')
            include_nopsf = False

        if self.model_images is None:
            if catalog is None:
                raise RuntimeError('ERROR - I need a catalog to make the model image first!')
            self.make_model_image(catalog, include_chi=include_chi, include_nopsf=include_nopsf, save=False)
        
        # Subtract
        self.residual_images = self.images - self.model_images

        # Save to file
        if save:

            if os.path.exists(self.auxhdu_path):
                self.logger.info(f'Saving image(s) to existing file, {self.auxhdu_path}')
                hdul = fits.open(self.auxhdu_path, mode='update')
                for i, band in enumerate(self.bands):
                    hdu_img = fits.ImageHDU(data=self.images[i], name=f'{band}_IMAGE')
                    hdul.append(hdu_img)
                    hdu_mod = fits.ImageHDU(data=self.model_images[i], name=f'{band}_MODEL')
                    hdul.append(hdu_mod)
                    if include_chi:
                        hdu_chi = fits.ImageHDU(data=self.chisq_images[i], name=f'{band}_CHI')
                        hdul.append(hdu_chi)
                    if include_nopsf:
                        hdu_nopsf = fits.ImageHDU(data=self.nopsf_images[i], name=f'{band}_NOPSF')
                        hdul.append(hdu_nopsf)

                    hdu_residual = fits.ImageHDU(data=self.residual_images[i], name=f'{band}_RESIDUAL')
                    hdul.append(hdu_residual)

                # Save
                hdul.flush()

            else:
                self.logger.info(f'brick.make_residual_image :: Saving image(s) to new file, s{self.auxhdu_path}')
                hdul = fits.HDUList()
                for i, band in enumerate(self.bands):
                    hdu_img = fits.ImageHDU(data=self.images[i], name=f'{band}_IMAGE')
                    hdul.append(hdu_img)
                    hdu_mod = fits.ImageHDU(data=self.model_images[i], name=f'{band}_MODEL')
                    hdul.append(hdu_mod)
                    if include_chi:
                        hdu_chi = fits.ImageHDU(data=self.chisq_images[i], name=f'{band}_CHI')
                        hdul.append(hdu_chi)
                    if include_nopsf:
                        hdu_nopsf = fits.ImageHDU(data=self.nopsf_images[i], name=f'{band}_NOPSF')
                        hdul.append(hdu_nopsf)

                    hdu_residual = fits.ImageHDU(data=self.residual_images[i], name=f'{band}_RESIDUAL')
                    hdul.append(hdu_residual)

                # Save
                hdul.writeto(self.auxhdu_path, overwrite=True)




