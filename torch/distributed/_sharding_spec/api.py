from abc import ABC
import torch
from typing import List, Union

from ._internals import is_valid_device

Device = Union[torch.device, int, str]

class PlacementSpec(ABC):
    """
    Base class representing the placement of an entity. Subclasses of this
    class can be used to specify customized placements which might not be
    covered by existing APIs.
    """
    pass

class DevicePlacementSpec(PlacementSpec):
    """
    Associates placement of an entity with a single device. The device can be a
    local device or a remote device specified by one of the following remote
    formats:

        1. "rank:<rank>/<device>" (ex: "rank:0/cuda:0").
        2. "<worker_name>/<device>" (ex: "trainer0/cuda:0").

    Args:
        device(str, :class:`torch.device`): The device to place the entity on.
    """
    def __init__(self, device: Device):
        super(DevicePlacementSpec, self).__init__()
        if not is_valid_device(device):
            raise ValueError(f'{device} is not a valid device')
        self._device = device

    @property
    def device(self) -> Device:
        """
        Retrieves the device for placement.
        """
        return self._device


class ShardingSpec(PlacementSpec):
    """
    Base class representing sharding specifications. It is special type of
    PlacementSpec.
    """
    pass


class ChunkShardingSpec(ShardingSpec):
    """
    This is a type of PlacementSpec that defines the placement as being sharded
    across multiple devices. In particular, it represents sharding a Tensor
    along a single dimension into equal chunks (similar to :meth:`torch.chunk`).

    The semantics of how a tensor is partitioned is inline with
    :meth:`torch.chunk`, where ``dim`` in torch.chunk corresponds to the
    specified ``dim`` and ``chunks`` in torch.chunk is the number of elements
    in the placement specified.

    Args:
        dim (int or str):
            The dimension to shard on, could be an integer representing the
            dimension or a string in case of named tensors where dimensions are
            named.
        placement(List[Device] or List[PlacementSpec]):
            Specifies the placement of each shard of the Tensor. The size of
            the list represents the number of shards to be created. This
            parameter can be a list of devices
            (ex: ["rank:0/cuda:0", "rank:1/cuda:1"]) or a list of custom
            placement specs.

            The device can be a local device or a remote device specified by one
            of the following remote formats:

                1. "rank:<rank>/<device>" (ex: "rank:0/cuda:0").
                2. "<worker_name>/<device>" (ex: "trainer0/cuda:0").
    """

    ShardingDim = Union[int, str]
    ShardPlacements = List[Union[Device, PlacementSpec]]

    def __init__(self, dim: ShardingDim, placements: ShardPlacements):
        super(ChunkShardingSpec, self).__init__()
        self._verify_dim(dim)
        self._verify_devices(placements)
        self._dim = dim
        self._placements = placements

    @staticmethod
    def _verify_devices(placements):
        if placements is None or len(placements) == 0:
            raise ValueError(f'None/Empty placement provided: {placement}')
        for dev in placements:
            if not isinstance(dev, PlacementSpec) and not is_valid_device(dev):
                raise ValueError(f'{dev} is not a valid device')

    @staticmethod
    def _verify_dim(dim):
        if not (isinstance(dim, int) or isinstance(dim, str)):
            raise ValueError(f'{dim} needs to either be an int or str')

    @property
    def dim(self) -> ShardingDim:
        """
        Retrieves the dimension to shard on.
        """
        return self._dim

    @property
    def placements(self) -> ShardPlacements:
        """
        Retrieves the shard placements.
        """
        return self._placements

class ShardMetadata(object):
    """
    Represents a shard of the overall Tensor including its
    offsets, lengths and device placement.

    Args:
        shard_offsets(List[int]): Offsets in the orignal tensor indicating
            the start offsets for this shard. Should have the same rank as
            the original tensor.
        shard_lengths(List[int]): Lengths indicating the length of each
            dimension for this shard. Should have the same rank as the
            original tensor.
        placement(List[Device or PlacementSpec]):
            Specifies the placement of each shard of the Tensor. The size of
            the list represents the number of shards to be created. This
            parameter can be a list of devices
            (ex: ["rank:0/cuda:0", "rank:1/cuda:1"]) or a list of custom
            placement specs.

            The device can be a local device or a remote device specified by one
            of the following remote formats:

                1. "rank:<rank>/<device>" (ex: "rank:0/cuda:0").
                2. "<worker_name>/<device>" (ex: "trainer0/cuda:0").
    """

    ShardPlacement = Union[Device, PlacementSpec]

    __slots__ = ['_shard_offsets', '_shard_lengths', '_placement']

    def __init__(
            self,
            shard_offsets: List[int],
            shard_lengths: List[int],
            placement: ShardPlacement):

        if not isinstance(placement, PlacementSpec) and not is_valid_device(placement):
            raise ValueError(f'{placement} is not a valid device')

        if len(shard_offsets) != len(shard_lengths):
            raise ValueError(
                f'shard_offsets and shard_lengths should have '
                f'the same number of elements, found {len(shard_offsets)} '
                f'and {shard_lengths} respectively')

        for i in range(len(shard_offsets)):
            if shard_offsets[i] < 0:
                raise ValueError('shard_offsets should be >=0')
            if shard_lengths[i] <= 0:
                raise ValueError('shard_lengths should be > 0')

        self._shard_offsets = shard_offsets
        self._shard_lengths = shard_lengths
        self._placement = placement

    def __repr__(self):
        return (
            f'ShardMetadata(shard_offsets: {self._shard_offsets}, '
            f'shard_lengths: {self._shard_lengths}, placement: {self._placement})'
        )

    @property
    def shard_offsets(self):
        return self._shard_offsets

    @property
    def shard_lengths(self):
        return self._shard_lengths

    @property
    def placement(self):
        return self._placement


class EnumerableShardingSpec(ShardingSpec):

    def __init__(self, shards: List[ShardMetadata]):
        """
        This is a type of PlacementSpec that allows users to specify a generic
        sharding scheme by enumerating exactly how each shard is laid out.

        Args:
            shards(List[ShardMetadata]): List of :class:`ShardMetadata` objects representing
                each shard.
        """
        super(EnumerableShardingSpec, self).__init__()
        if len(shards) == 0:
            raise ValueError(f'Empty shard list provided: {shards}')

        # Validate each shard has same rank.
        rank = -1
        for shard in shards:
            if rank != -1 and rank != len(shard.shard_offsets):
                raise ValueError(f'Found inconsistent ranks for shards: {rank} and {len(shard.shard_offsets)}')
            rank = len(shard.shard_offsets)

        self._validate_non_overlapping(shards)

        self._shards = shards

    @staticmethod
    def _validate_non_overlapping(shards: List[ShardMetadata]):
        """
        Ensures none of the shards overlap with each other.
        """
        # TODO: evaluate optimizing this if needed.
        for i in range(len(shards)):
            for j in range(i + 1, len(shards)):
                if EnumerableShardingSpec._check_shard_pair_overlap(shards[i], shards[j]):
                    raise ValueError(f'Shards {shards[i]} and {shards[j]} overlap')

    @staticmethod
    def _check_shard_pair_overlap(shard1: ShardMetadata, shard2: ShardMetadata):
        """
        Checks if two shards overlap.
        """

        # For each dim of each shard, check if one shard resides on the other
        # end of second shard with respect to that dim. As an example for a 2D
        # shard, we would check if one shard is above or on the left of the
        # other shard.
        ndims = len(shard1.shard_offsets)
        for i in range(ndims):
            if shard1.shard_offsets[i] >= shard2.shard_offsets[i] + shard2.shard_lengths[i]:
                return False
            if shard2.shard_offsets[i] >= shard1.shard_offsets[i] + shard1.shard_lengths[i]:
                return False

        return True

    @property
    def shards(self):
        return self._shards

    def check_tensor(self, tensor: torch.Tensor) -> None:
        """
        Checks if the sharding spec is compatible with the provided tensor.

        Args:
            tensor(torch.Tensor): Tensor to verify.
        Raises:
            ``ValueError`` if not compatible.
        """

        # If the tensor's volume matches the total volume of all shards and
        # all shard boundaries are within tensor dims, we have a compatible
        # sharding spec for this tensor. Note that we have already verified
        # we don't have overlapping shards.
        tensor_rank = len(tensor.size())
        shards_rank = len(self._shards[0].shard_offsets)
        if tensor_rank != shards_rank:
            raise ValueError(f'Rank of tensor is {tensor_rank}, but shards rank is {shards_rank}')

        total_shard_volume = 0
        tensor_dims = tensor.size()
        for shard in self._shards:
            shard_volume = 1
            for i, shard_length in enumerate(shard.shard_lengths):
                shard_volume *= shard_length
                if shard.shard_offsets[i] + shard.shard_lengths[i] > tensor_dims[i]:
                    raise ValueError(
                        f'Shard offset {shard.shard_offsets[i]} and length'
                        f'{shard.shard_lengths[i]} exceeds tensor dim: {tensor_dims[i]} for shard {shard}')
            total_shard_volume += shard_volume

        tensor_volume = 1
        for size in tensor_dims:
            tensor_volume *= size

        if total_shard_volume != tensor_volume:
            # TODO: Can we improve this error message to point out the gaps?
            raise ValueError(
                f'Total volume of shards: {total_shard_volume}'
                f'does not match tensor volume: {tensor_volume}, in other words'
                f' all the individual shards do not cover the entire tensor')
