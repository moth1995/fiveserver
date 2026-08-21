# try to use epoll reactor if available
try:
    from twisted.internet import epollreactor
    epollreactor.install()
except:
    pass

from twisted.application.internet import TCPServer
from twisted.application.service import Application
from twisted.web.server import Site
from twisted.web import resource, server
from twisted.internet import reactor

from fiveserver.config import FiveServerConfig, YamlConfig, DatabaseConfig
from fiveserver.protocol import PacketServiceFactory
from fiveserver.protocol import pes2008
from fiveserver.protocol.pes2008_web import (
    formatEmptyRankingResponse, formatRankingPage, formatRankingResponse,
    resolveRankingType)
from fiveserver.register import RegistrationResource
from fiveserver import storagecontroller, log
from fiveserver import admin, data6, logic
from datetime import datetime
import os


application = Application("Eightserver application")
fsroot = os.environ.get('FSROOT','.')

scfg = YamlConfig(fsroot + '/etc/conf/eightserver.yaml')
log.setDebug(scfg.Debug)
dbConfig = DatabaseConfig(**scfg.DB)
storageController = storagecontroller.StorageController(
    dbConfig.getReadPool(), dbConfig.getWritePool())

keepAliveManager = storagecontroller.KeepAliveManager(
    storageController,
    dbConfig.ConnectionPool.keepAliveInterval,
    dbConfig.ConnectionPool.keepAliveQuery)
keepAliveManager.start()

userData = data6.UserData(storageController)
profileData = data6.ProfileData(storageController)
matchData = data6.MatchData(storageController)
profileLogic = logic.ProfileLogic(matchData, profileData)
config = FiveServerConfig(
    scfg, dbConfig, userData, profileData, matchData, profileLogic)

for gameName,port in scfg.GamePorts.items():
    factory = PacketServiceFactory(config)
    factory.protocol = pes2008.NewsProtocol
    if hasattr(scfg, 'Greeting'):
        factory.protocol.GREETING['text'] = scfg.Greeting['text']
    if hasattr(scfg, 'ServerName'):
        factory.protocol.SERVER_NAME = scfg.ServerName
    service = TCPServer(port, factory, interface=config.interface)
    service.setServiceParent(application)

for protocol,port in [
        (pes2008.MainService,scfg.NetworkServer['mainService']),
        (pes2008.NetworkMenuService,scfg.NetworkServer['networkMenuService']),
        (pes2008.LoginServicePES2008,scfg.NetworkServer['loginService']['pes2008']),
        ]:
    factory = PacketServiceFactory(config)
    factory.protocol = protocol
    service = TCPServer(port, factory, interface=config.interface)
    service.setServiceParent(application)

class RankingResource(resource.Resource):
    """Twisted endpoint used by the PES2008 ranking browser and client."""

    isLeaf = True

    def __init__(self, config):
        resource.Resource.__init__(self)
        self.config = config

    def render_GET(self, request):
        request.setHeader('Content-Type', 'text/html; charset=utf-8')
        return formatRankingPage()

    def render_POST(self, request):
        values = {}
        for name in (b'type', b'from', b'records', b'pid', b'flag'):
            rawValues = request.args.get(name, [b''])
            values[name.decode('ascii')] = rawValues[0].decode(
                'ascii', 'replace')
        log.msg(
            'PES2008 ranking query: '
            'type=%(type)s from=%(from)s records=%(records)s '
            'pid=%(pid)s flag=%(flag)s' % values)

        try:
            rankingType = int(values['type'])
            firstRecord = max(1, int(values['from']))
            recordCount = max(1, min(20, int(values['records'])))
            profileId = int(values['pid'])
        except ValueError:
            request.setResponseCode(400)
            request.setHeader('Content-Type', 'text/plain; charset=us-ascii')
            return formatEmptyRankingResponse()

        now = datetime.now()
        try:
            division, playedFrom, playedTo = resolveRankingType(
                rankingType, now)
        except ValueError:
            request.setResponseCode(400)
            request.setHeader('Content-Type', 'text/plain; charset=us-ascii')
            return formatEmptyRankingResponse(now)

        result = self.config.profileData.getRanking(
            firstRecord - 1, recordCount, division,
            playedFrom, playedTo, profileId)

        def _writeRanking(result):
            total, entries, playerEntry = result
            for entry in entries:
                entry['division'] = self.config.ratingMath.getDivision(
                    entry['points'])
            if playerEntry is not None:
                playerEntry['division'] = self.config.ratingMath.getDivision(
                    playerEntry['points'])
            request.setHeader(
                'Content-Type', 'text/plain; charset=us-ascii')
            request.write(formatRankingResponse(
                entries, total, playerEntry, now))
            request.finish()

        def _rankingFailed(error):
            log.msg('ERROR: PES2008 ranking query failed: %s' % error.value)
            request.setResponseCode(500)
            request.setHeader(
                'Content-Type', 'text/plain; charset=us-ascii')
            request.write(formatEmptyRankingResponse(now))
            request.finish()

        result.addCallback(_writeRanking)
        result.addErrback(_rankingFailed)
        return server.NOT_DONE_YET


class WebRootResource(RegistrationResource):
    """Registration root with the PES2008 web endpoints mounted below it."""

    isLeaf = False

    def __init__(self, config, webDir):
        resource.Resource.__init__(self)
        RegistrationResource.__init__(self, config, webDir)
        pes2008Root = resource.Resource()
        pes2008Root.putChild(b'ranking', RankingResource(config))
        self.putChild(b'pes2008', pes2008Root)

    def getChild(self, path, request):
        # Preserve RegistrationResource's legacy catch-all routes.
        return self


# registration and PES2008 ranking web-service
registrationServer = Site(WebRootResource(config, fsroot + '/web6'))
service = TCPServer(scfg.WebInterface['port'], registrationServer,
    interface=config.interface)
service.setServiceParent(application)

class ServerContextFactory:
    def getContext(self):
        from OpenSSL import SSL
        ctx = SSL.Context(SSL.SSLv23_METHOD)
        ctx.use_privatekey_file(
            fsroot + '/%s/serverkey.pem' % adminConfig.KeysDirectory)
        ctx.use_certificate_file(
            fsroot + '/%s/servercert.pem' % adminConfig.KeysDirectory)
        return ctx

adminConfig = YamlConfig(fsroot + '/etc/conf/admin8.yaml')

# server admin web-service (HTTPS, authentication)
adminRoot = admin.AdminRootResource(adminConfig, config)
adminRoot.putChild(b'', adminRoot)
adminRoot.putChild(b'home', adminRoot)
adminRoot.putChild(b'xsl', admin.XslResource(adminConfig))
adminRoot.putChild(b'log', admin.LogResource(adminConfig, config))
adminRoot.putChild(b'biglog', admin.LogResource(adminConfig, config))
usersResource = admin.UsersResource(adminConfig, config)
adminRoot.putChild(b'users', usersResource)
usersResource.putChild(
    b'online', admin.UsersOnlineResource(adminConfig, config))
adminRoot.putChild(b'stats', admin.StatsResource(adminConfig, config))
adminRoot.putChild(b'profiles', admin.ProfilesResource(adminConfig, config))
adminRoot.putChild(
    b'userlock', admin.UserLockResource(adminConfig, config))
adminRoot.putChild(
    b'userkill', admin.UserKillResource(adminConfig, config))
adminRoot.putChild(b'debug', admin.DebugResource(adminConfig, config))
adminRoot.putChild(b'maxusers', admin.MaxUsersResource(adminConfig, config))
adminRoot.putChild(b'settings', admin.StoreSettingsResource(adminConfig, config))
adminRoot.putChild(b'roster', admin.RosterResource(adminConfig, config))
adminRoot.putChild(b'banned', admin.BannedResource(adminConfig, config))
adminRoot.putChild(b'ban-add', admin.BanAddResource(adminConfig, config))
adminRoot.putChild(
    b'ban-remove', admin.BanRemoveResource(adminConfig, config))
adminRoot.putChild(b'server-ip', admin.ServerIpResource(adminConfig, config))
adminRoot.putChild(b'ps', admin.ProcessInfoResource(adminConfig, config))
adminServer = Site(adminRoot)
reactor.listenSSL(adminConfig.AdminPort, adminServer, ServerContextFactory(),
    interface=config.interface)

# stats web-service (HTTP, no authentication)
# Only available for requests from localhost
statsRoot = admin.StatsRootResource(adminConfig, config, False)
statsRoot.putChild(b'', statsRoot)
statsRoot.putChild(b'home', statsRoot)
statsRoot.putChild(b'xsl', admin.XslResource(adminConfig))
usersResource = admin.UsersResource(adminConfig, config, False)
statsRoot.putChild(b'users', usersResource)
usersResource.putChild(
    b'online', admin.UsersOnlineResource(adminConfig, config, False))
statsRoot.putChild(b'stats', admin.StatsResource(adminConfig, config, False))
statsRoot.putChild(
    b'profiles', admin.ProfilesResource(adminConfig, config, False))
statsRoot.putChild(b'ps', admin.ProcessInfoResource(adminConfig, config, False))
statsServer = Site(statsRoot)
statsService = TCPServer(adminConfig.AdminPort+1, statsServer,
    interface='127.0.0.1') # restrict to localhost requests only
statsService.setServiceParent(application)
