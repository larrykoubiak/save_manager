"""Memory card capacity in 1 KiB blocks, excluding reserved storage and ECC."""

from vendor.mymcplusplus.mymcplusplus.ps2mc import PS2MC_FAT_ALLOCATED_BIT


def card_capacity(card):
    limit = min(card.allocatable_cluster_end, card.allocatable_cluster_limit)
    free_clusters = sum(not (card.lookup_fat(i) & PS2MC_FAT_ALLOCATED_BIT) for i in range(limit))
    total = limit * card.cluster_size // 1024
    free = free_clusters * card.cluster_size // 1024
    return {'formatted': True, 'total_blocks': total, 'free_blocks': free,
            'used_blocks': total - free}
