# Copyright (c) OpenMMLab. All rights reserved.
from typing import Optional, Tuple

import numpy as np

from mmpose.codecs.base import BaseKeypointCodec
from mmpose.codecs.utils.gaussian_heatmap import \
    generate_unbiased_gaussian_heatmaps
from mmpose.codecs.utils.post_processing import get_heatmap_maximum
from mmpose.registry import KEYPOINT_CODECS


@KEYPOINT_CODECS.register_module()
class SKPSHeatmap(BaseKeypointCodec):
    """Generate heatmap the same with MSRAHeatmap, and produce offside within x
    and y directions.

    Note:

        - instance number: N
        - keypoint number: K
        - keypoint dimension: D
        - image size: [w, h]
        - heatmap size: [W, H]

    Encoded:

        - heatmaps (np.ndarray): The generated heatmap in shape (K, H, W)
            where [W, H] is the `heatmap_size`
        - offside (np.ndarray): The generated offside in x andy direction
            in shape (2K, H, W) where [W, H] is the `heatmap_size`
        - keypoint_weights (np.ndarray): The target weights in shape (N, K)

    Args:
        input_size (tuple): Image size in [w, h]
        heatmap_size (tuple): Heatmap size in [W, H]
        sigma (float): The sigma value of the Gaussian heatmap
    """

    def __init__(self, input_size: Tuple[int, int],
                 heatmap_size: Tuple[int, int], sigma: float) -> None:
        super().__init__()
        self.input_size = input_size
        self.heatmap_size = heatmap_size
        self.sigma = sigma
        self.scale_factor = (np.array(input_size) /
                             heatmap_size).astype(np.float32)

        self.y_range, self.x_range = np.meshgrid(
            np.arange(0, self.heatmap_size[1]),
            np.arange(0, self.heatmap_size[0]),
            indexing='ij')

    def encode(self,
               keypoints: np.ndarray,
               keypoints_visible: Optional[np.ndarray] = None) -> dict:
        """Encode keypoints into heatmaps. Note that the original keypoint
        coordinates should be in the input image space.

        Args:
            keypoints (np.ndarray): Keypoint coordinates in shape (N, K, D)
            keypoints_visible (np.ndarray): Keypoint visibilities in shape
                (N, K)

        Returns:
            dict:
            - heatmaps (np.ndarray): The generated heatmap in shape
                (K, H, W) where [W, H] is the `heatmap_size`
            - offside (np.ndarray): The generated offside in x and y
                directions in shape (2*K, H, W) where [W, H] is the
                `heatmap_size`
            - keypoint_weights (np.ndarray): The target weights in shape
                (N, K)
        """

        assert keypoints.shape[0] == 1, (
            f'{self.__class__.__name__} only support single-instance '
            'keypoint encoding')

        if keypoints_visible is None:
            keypoints_visible = np.ones(keypoints.shape[:2], dtype=np.float32)

        heatmaps, keypoint_weights = generate_unbiased_gaussian_heatmaps(
            heatmap_size=self.heatmap_size,
            keypoints=keypoints / self.scale_factor,
            keypoints_visible=keypoints_visible,
            sigma=self.sigma)

        offside_heatmap = self.generate_offside_heatmap(
            heatmap_size=self.heatmap_size,
            keypoints=keypoints / self.scale_factor,
        )

        encoded = dict(
            heatmaps=heatmaps,
            keypoint_weights=keypoint_weights[0],
            displacements=offside_heatmap)

        return encoded

    def generate_offside_heatmap(
        self,
        heatmap_size: Tuple[int, int],
        keypoints: np.ndarray,
    ):

        N, K, _ = keypoints.shape
        W, H = heatmap_size

        # batchsize 1
        keypoints = keypoints[0]

        # caution: there will be a broadcast which produce
        # offside_x and offside_y with shape 64x64x98

        offside_x = keypoints[:, 0] - np.expand_dims(self.x_range, axis=-1)
        offside_y = keypoints[:, 1] - np.expand_dims(self.y_range, axis=-1)

        offside_map = np.concatenate([offside_x, offside_y], axis=-1)

        offside_map = np.transpose(offside_map, axes=[2, 0, 1])

        return offside_map

    def decode(self, encoded: np.ndarray,
               offside: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Decode keypoint coordinates from heatmaps. The decoded keypoint
        coordinates are in the input image space.

        Args:
            encoded (np.ndarray): Heatmaps in shape (K, H, W)

        Returns:
            tuple:
            - keypoints (np.ndarray): Decoded keypoint coordinates in shape
                (N, K, D)
            - scores (np.ndarray): The keypoint scores in shape (N, K). It
                usually represents the confidence of the keypoint prediction
        """
        heatmaps = encoded.copy()

        offside = offside.copy()

        K, H, W = heatmaps.shape

        keypoints, scores = get_heatmap_maximum(heatmaps)

        offside_x = offside[:K, ...]
        offside_y = offside[K:, ...]

        keypoints_interger = keypoints.astype(np.int32)
        keypoints_decimal = np.zeros_like(keypoints)

        for i in range(K):
            [x, y] = keypoints_interger[i]
            if x < 0 or y < 0:
                x = y = 0

            # caution: torhc tensor shape is nchw, so indx should be i,y,x
            keypoints_decimal[i][0] = x + offside_x[i, y, x]
            keypoints_decimal[i][1] = y + offside_y[i, y, x]

        # Restore the keypoint scale
        keypoints_decimal = keypoints_decimal * self.scale_factor

        return keypoints_decimal[None], scores[None]
