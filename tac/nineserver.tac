# try to use epoll reactor if available
try:
    from twisted.internet import epollreactor

    epollreactor.install()
except:
    pass

from twisted.application.internet import TCPServer, SSLServer
from twisted.internet.interfaces import IProtocolFactory, ILoggingContext
from twisted.internet.ssl import DefaultOpenSSLContextFactory
from zope.interface import implementer_only
from twisted.application.service import Application
from twisted.web.server import Site
from twisted.internet import reactor

from fiveserver.config import FiveServerConfig, YamlConfig, DatabaseConfig
from fiveserver.protocol import PacketServiceFactory
from fiveserver.protocol import pes2009
from fiveserver.register import RegistrationResource
from fiveserver import storagecontroller, log
from fiveserver import admin, data9, logic
import os
from urllib.parse import urlsplit

application = Application("NineServer application")
fsroot = os.environ.get("FSROOT", ".")

scfg = YamlConfig(fsroot + "/etc/conf/nineserver.yaml")
log.setDebug(scfg.Debug)
dbConfig = DatabaseConfig(**scfg.DB)
storageController = storagecontroller.StorageController(
    dbConfig.getReadPool(), dbConfig.getWritePool()
)

keepAliveManager = storagecontroller.KeepAliveManager(
    storageController,
    dbConfig.ConnectionPool.keepAliveInterval,
    dbConfig.ConnectionPool.keepAliveQuery,
)
keepAliveManager.start()

userData = data9.UserData(storageController)
profileData = data9.ProfileData(storageController)
matchData = data9.MatchData(storageController)
profileLogic = logic.ProfileLogic(matchData, profileData)
config = FiveServerConfig(
    scfg, dbConfig, userData, profileData, matchData, profileLogic
)

for gameName, port in scfg.GamePorts.items():
    factory = PacketServiceFactory(config)
    factory.protocol = pes2009.NewsProtocol
    if hasattr(scfg, "Greeting"):
        factory.protocol.GREETING["text"] = scfg.Greeting["text"]
    if hasattr(scfg, "ServerName"):
        factory.protocol.SERVER_NAME = scfg.ServerName
    service = TCPServer(port, factory, interface=config.interface)
    service.setServiceParent(application)

for protocol, port in [
    (pes2009.MainService, scfg.NetworkServer["mainService"]),
    (pes2009.NetworkMenuService, scfg.NetworkServer["networkMenuService"]),
    (pes2009.LoginServicePES2009, scfg.NetworkServer["loginService"]["pes2009"]),
]:
    factory = PacketServiceFactory(config)
    factory.protocol = protocol
    service = TCPServer(port, factory, interface=config.interface)
    service.setServiceParent(application)

# registration web-service
registrationServer = Site(RegistrationResource(config, fsroot + "/web9"))
service = TCPServer(
    scfg.WebInterface["port"], registrationServer, interface=config.interface
)
service.setServiceParent(application)

# same site over TLS: the game logs in via https://.../auth.php
# (cert from scripts/make_pes2009_cert.py, trusted in the Windows Root store)
@implementer_only(IProtocolFactory, ILoggingContext)
class Http1Site(Site):
    # no ALPN: Twisted 21 sets it on the context after creating the TLS
    # connection, which pyOpenSSL >= 24 rejects. The game only speaks HTTP/1.1.
    pass


# the game uses TLS for every port but 80, whatever the URL scheme says
webBasePort = urlsplit(getattr(scfg, "WebBaseUrl", "") or "").port
if webBasePort not in (None, 80, scfg.WebInterface.get("sslPort")):
    log.msg(
        "WARNING: WebBaseUrl port %s is not WebInterface.sslPort: the game "
        "will speak HTTPS to it (only port 80 is plain HTTP)" % webBasePort
    )

if scfg.WebInterface.get("sslPort"):
    webTls = DefaultOpenSSLContextFactory(
        fsroot + "/" + scfg.WebInterface.get("sslKey", "etc/keys/pes2009web-key.pem"),
        fsroot + "/" + scfg.WebInterface.get("sslCert", "etc/keys/pes2009web-cert.pem"),
    )
    service = SSLServer(
        scfg.WebInterface["sslPort"],
        Http1Site(registrationServer.resource),
        webTls,
        interface=config.interface,
    )
    service.setServiceParent(application)


class ServerContextFactory:
    def getContext(self):
        from OpenSSL import SSL

        ctx = SSL.Context(SSL.SSLv23_METHOD)
        ctx.use_privatekey_file(
            fsroot + "/%s/serverkey.pem" % adminConfig.KeysDirectory
        )
        ctx.use_certificate_file(
            fsroot + "/%s/servercert.pem" % adminConfig.KeysDirectory
        )
        return ctx


adminConfig = YamlConfig(fsroot + "/etc/conf/admin9.yaml")

# server admin web-service (HTTPS, authentication)
adminRoot = admin.AdminRootResource(adminConfig, config)
adminRoot.putChild(b"", adminRoot)
adminRoot.putChild(b"home", adminRoot)
adminRoot.putChild(b"xsl", admin.XslResource(adminConfig))
adminRoot.putChild(b"log", admin.LogResource(adminConfig, config))
adminRoot.putChild(b"biglog", admin.LogResource(adminConfig, config))
usersResource = admin.UsersResource(adminConfig, config)
adminRoot.putChild(b"users", usersResource)
usersResource.putChild(b"online", admin.UsersOnlineResource(adminConfig, config))
adminRoot.putChild(b"stats", admin.StatsResource(adminConfig, config))
adminRoot.putChild(b"profiles", admin.ProfilesResource(adminConfig, config))
adminRoot.putChild(b"userlock", admin.UserLockResource(adminConfig, config))
adminRoot.putChild(b"userkill", admin.UserKillResource(adminConfig, config))
adminRoot.putChild(b"debug", admin.DebugResource(adminConfig, config))
adminRoot.putChild(b"maxusers", admin.MaxUsersResource(adminConfig, config))
adminRoot.putChild(b"settings", admin.StoreSettingsResource(adminConfig, config))
adminRoot.putChild(b"roster", admin.RosterResource(adminConfig, config))
adminRoot.putChild(b"banned", admin.BannedResource(adminConfig, config))
adminRoot.putChild(b"ban-add", admin.BanAddResource(adminConfig, config))
adminRoot.putChild(b"ban-remove", admin.BanRemoveResource(adminConfig, config))
adminRoot.putChild(b"server-ip", admin.ServerIpResource(adminConfig, config))
adminRoot.putChild(b"ps", admin.ProcessInfoResource(adminConfig, config))
adminServer = Site(adminRoot)
reactor.listenSSL(
    adminConfig.AdminPort,
    adminServer,
    ServerContextFactory(),
    interface=config.interface,
)

# stats web-service (HTTP, no authentication)
# Only available for requests from localhost
statsRoot = admin.StatsRootResource(adminConfig, config, False)
statsRoot.putChild(b"", statsRoot)
statsRoot.putChild(b"home", statsRoot)
statsRoot.putChild(b"xsl", admin.XslResource(adminConfig))
usersResource = admin.UsersResource(adminConfig, config, False)
statsRoot.putChild(b"users", usersResource)
usersResource.putChild(b"online", admin.UsersOnlineResource(adminConfig, config, False))
statsRoot.putChild(b"stats", admin.StatsResource(adminConfig, config, False))
statsRoot.putChild(b"profiles", admin.ProfilesResource(adminConfig, config, False))
statsRoot.putChild(b"ps", admin.ProcessInfoResource(adminConfig, config, False))
statsServer = Site(statsRoot)
statsService = TCPServer(
    adminConfig.AdminPort + 1, statsServer, interface="127.0.0.1"
)  # restrict to localhost requests only
statsService.setServiceParent(application)
