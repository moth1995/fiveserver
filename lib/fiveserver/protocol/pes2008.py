"""
Protocol implementations for PES2008

Duplicates the PES6 implementation (fiveserver.protocol.pes6) as-is;
PES2008 uses the same network protocol as PES6/WE2007.
"""

from fiveserver.protocol import pes6


class NewsProtocol(pes6.NewsProtocol):
    """
    News-service for PES2008
    """


class LoginService(pes6.LoginService):
    """
    Login-service for PES2008
    """


class LoginServicePES2008(LoginService):
    """
    Specific implementation of login service for PES2008
    """


class NetworkMenuService(pes6.MainService):
    """
    PES2008 implementation.
    The service that communicates with the player, when
    he/she is in the "NETWORK MENU" mode.
    """


class MainService(NetworkMenuService):
    """
    PES2008 implementation
    The main game server, which keeps track of matches, goals
    and other important statistics.
    """
