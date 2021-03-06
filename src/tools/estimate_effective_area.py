
import os
import sys
import numpy as np

from astropy.table import Table
from astropy.io import fits

fname_tab = sys.argv[1]
dir_segmaps = sys.argv[2]
dir_bricks = sys.argv[3]
pixelscale = float(sys.argv[4])
N_try = int(sys.argv[5])

# open catalog
tab = Table.read(fname_tab)

# get brick ids
brick_id = tab['brick_id']
blob_id = tab['blob_id']


good_px_deep_seg = 0
good_px_deep_blob = 0

bad_px_deep_seg = 0
bad_px_deep_blob = 0

good_px_udeep_seg = 0
good_px_udeep_blob = 0

bad_px_udeep_seg = 0
bad_px_udeep_blob = 0

if N_try > len(np.unique(brick_id)):
        N_try = len(np.unique(brick_id))

print(f'Will try {N_try}/{len(np.unique(brick_id))} bricks!')

count = 0
for i, bid in enumerate(np.unique(brick_id)):
        print()
        print(bid)

        if (~tab['VALID_SOURCE'][tab['brick_id']==bid]).all():
                print('ALL INVALID!')
                continue

        # open segmap
        with fits.open(os.path.join(dir_segmaps, f'B{bid}_SEGMAPS.fits')) as hdul:
                print('Opening files...')
                blobmap = hdul['BLOBMAP'].data
                segmap = hdul['SEGMAP'].data
                mask = fits.getdata(os.path.join(dir_bricks, f'B{bid}_NDETECTION_W2004_H2004.fits'), 'DETECTION_MASK')

                print('Getting subcatalog')
                # get subcatalog for this brick
                subcat = tab[brick_id==bid]
                
                
                # apply the mask to both first
                masked_px = np.sum(mask)
                print(f'Total masked area: {masked_px} px')
                total_px = (mask.shape[0] - 200) * (mask.shape[1] - 200)
                print(f'Total area: {total_px} px')

                print('Applying mask')
                blobmap[mask] = 0
                segmap[mask] = 0
                

                # DEEP FLAG
                subcat_deep = subcat[subcat['FLAG_shallowstripes']]
                blob_id = subcat_deep['blob_id']
                source_id = subcat_deep['source_id']
                print(f'Number of sources in deep region: {len(subcat_deep)}')

                # from the blobmap
                inval_blob_deep = np.unique(blob_id[~subcat_deep['VALID_SOURCE']])
                print(f'Number of invalid blobs: {len(inval_blob_deep)}')

                inval_px = np.sum(np.isin(blobmap, inval_blob_deep))
                ok_px = total_px - masked_px - inval_px
                bad_px = masked_px + inval_px

                print(f'Based on the blobmap:')
                print(f'    Total inval area: {inval_px} px ({100*inval_px/total_px}%)')
                print(f'    Total ok area: {ok_px} px ({100*ok_px/total_px}%)')
                print(f'    Total bad area: {bad_px} px ({100*bad_px/total_px}%)')

                good_px_deep_blob += ok_px
                bad_px_deep_blob += bad_px

                # from the seg map
                inval_seg_deep = source_id[~subcat_deep['VALID_SOURCE']]
                print(f'Number of invalid sources: {len(inval_seg_deep)}')

                inval_px = np.sum(np.isin(segmap, inval_seg_deep))
                ok_px = total_px - masked_px - inval_px
                bad_px = masked_px + inval_px
                
                print(f'Based on the segmap:')
                print(f'    Total inval area: {inval_px} px ({100*inval_px/total_px}%)')
                print(f'    Total ok area: {ok_px} px ({100*ok_px/total_px}%)')
                print(f'    Total bad area: {bad_px} px ({100*bad_px/total_px}%)')

                good_px_deep_seg += ok_px
                bad_px_deep_seg += bad_px


                # UDEEP FLAG
                subcat_udeep = subcat[subcat['FLAG_deepstripes']]
                blob_id = subcat_udeep['blob_id']
                source_id = subcat_udeep['source_id']
                print(f'Number of sources in udeep region: {len(subcat_udeep)}')

                # from the blobmap
                inval_blob_deep = blob_id[~subcat_udeep['VALID_SOURCE']]
                print(f'Number of invalid blobs: {len(inval_blob_deep)}')

                inval_px = np.sum(np.isin(blobmap, inval_blob_deep))
                ok_px = total_px - masked_px - inval_px
                bad_px = masked_px + inval_px

                print(f'Based on the blobmap:')
                print(f'    Total inval area: {inval_px} px ({100*inval_px/total_px}%)')
                print(f'    Total ok area: {ok_px} px ({100*ok_px/total_px}%)')
                print(f'    Total bad area: {bad_px} px ({100*bad_px/total_px}%)')

                good_px_udeep_blob += ok_px
                bad_px_udeep_blob += bad_px

                # from the seg map
                inval_seg_deep = source_id[~subcat_udeep['VALID_SOURCE']]
                print(f'Number of invalid sources: {len(inval_seg_deep)}')

                inval_px = np.sum(np.isin(segmap, inval_seg_deep))
                ok_px = total_px - masked_px - inval_px
                bad_px = masked_px + inval_px
                
                print(f'Based on the segmap:')
                print(f'    Total inval area: {inval_px} px ({100*inval_px/total_px}%)')
                print(f'    Total ok area: {ok_px} px ({100*ok_px/total_px}%)')
                print(f'    Total bad area: {bad_px} px ({100*bad_px/total_px}%)')

                good_px_udeep_seg += ok_px
                bad_px_udeep_seg += bad_px

        count += 1
        if count >= N_try:
            print(f'Exceeded maximum number of bricks to attempt {N_try}. Exiting.')
            break

print('From blobmaps:')
print(f'Final good/bad area over {i} bricks in deep: {good_px_deep_blob}/{bad_px_deep_blob} px')
print(f'Final good/bad area over {i} bricks in udeep: {good_px_udeep_blob}/{bad_px_udeep_blob} px')

print('From segmaps:')
print(f'Final good/bad area over {i} bricks in deep: {good_px_deep_seg}/{bad_px_deep_seg} px')
print(f'Final good/bad area over {i} bricks in udeep: {good_px_udeep_seg}/{bad_px_udeep_seg} px')


good_px_deep_seg *= (pixelscale / 3600)**2
good_px_deep_blob *= (pixelscale / 3600)**2

good_px_udeep_seg *= (pixelscale / 3600)**2
good_px_udeep_blob *= (pixelscale / 3600)**2

bad_px_deep_seg *= (pixelscale / 3600)**2
bad_px_deep_blob *= (pixelscale / 3600)**2

bad_px_udeep_seg *= (pixelscale / 3600)**2
bad_px_udeep_blob *= (pixelscale / 3600)**2

print()
print('From blobmaps:')
print(f'Final good/bad area over {i} bricks in deep: {good_px_deep_blob}/{bad_px_deep_blob} deg2')
print(f'Final good/bad area over {i} bricks in udeep: {good_px_udeep_blob}/{bad_px_udeep_blob} deg2')

print('From segmaps:')
print(f'Final good/bad area over {i} bricks in deep: {good_px_deep_seg}/{bad_px_deep_seg} deg2')
print(f'Final good/bad area over {i} bricks in udeep: {good_px_udeep_seg}/{bad_px_udeep_seg} deg2')
