"""PlayStation 2 game plugins."""

from .gta3 import GTAIIIUS
from .burnout3 import Burnout3US
from .ffx import FFXInternational
from .ar_tonelico import ArTonelicoUS
from .dothack import DotHackInfectionUS
from .katamari import KatamariDamacyUS
from .steambot import SteambotChroniclesUS
from .thps4 import THPS4CareerUS
from .thpg import THPGCareerUS
from .we_love_katamari import WeLoveKatamariUS
from .monster_hunter import MonsterHunterUS
from .odin_sphere import OdinSphereUS
from .profiles import CrazyTaxiUS, DOA2HardcoreUS, TenkaichiUS, KOFOrochiUS, SoulcaliburIIUS

PLUGINS = (
    CrazyTaxiUS, DOA2HardcoreUS, TenkaichiUS, KOFOrochiUS, SoulcaliburIIUS,
    FFXInternational, ArTonelicoUS, DotHackInfectionUS, KatamariDamacyUS,
    WeLoveKatamariUS, MonsterHunterUS, OdinSphereUS, SteambotChroniclesUS,
    THPS4CareerUS, THPGCareerUS, GTAIIIUS, Burnout3US,
)
