"""PlayStation game plugins."""

from .ffix import FinalFantasyIXUS
from .chrono_cross import ChronoCrossUS
from .tales_of_phantasia import TalesOfPhantasiaTranslated
from .fft import FinalFantasyTacticsUS
from .saga_frontier_2 import SaGaFrontier2US
from .tomba import TombaUS
from .xenogears import XenogearsUS
from .mgs import MetalGearSolidUS

PLUGINS = (FinalFantasyIXUS,ChronoCrossUS,TalesOfPhantasiaTranslated,
           FinalFantasyTacticsUS,SaGaFrontier2US,TombaUS,XenogearsUS,MetalGearSolidUS)
