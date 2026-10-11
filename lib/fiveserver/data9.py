"""
Data-access layer for PES2009 (nineserver, sql/schema9.sql).
Same as data6, plus the per-profile settings sent in packet 0x4120.
"""

from twisted.internet import defer

from fiveserver import data6

# schema9.sql `settings` columns, in PES2009 0x4120 wire order
SETTINGS_COLUMNS = (
    'unk_0', 'birth_year', 'birth_month', 'birth_day', 'unk_8', 'country',
    'unk_11', 'fav_team', 'fav_player', 'unk_19', 'unk_20', 'unk_24')

# what the game sends for a profile with nothing set ("unregistered")
DEFAULT_SETTINGS = dict.fromkeys(SETTINGS_COLUMNS, 0)
DEFAULT_SETTINGS['fav_team'] = 0xFFFF

UserData = data6.UserData

# ranking stats only count non-friendly matches (matches.friendly)
_RANKED = ('FROM matches, matches_played '
           'WHERE matches.id=matches_played.match_id AND profile_id=%s '
           'AND matches.friendly=0 ')


class MatchData(data6.MatchData):
    """
    data6.MatchData with a per-match friendly flag: every match is stored,
    but wins/losses/draws/goals/streaks (and so points) ignore friendlies.
    Play time and last-teams-used still include them.
    """

    @defer.inlineCallbacks
    def _count(self, profileId, condition=''):
        rows = yield self.dbController.dbRead(
            0, 'SELECT count(matches.id) ' + _RANKED + condition, profileId)
        defer.returnValue(rows[0][0])

    def getGames(self, profileId):
        return self._count(profileId)

    def getWins(self, profileId):
        return self._count(profileId, 'AND ((home=1 and score_home>score_away) OR '
                                      '(home=0 and score_home<score_away))')

    def getLosses(self, profileId):
        return self._count(profileId, 'AND ((home=1 and score_home<score_away) OR '
                                      '(home=0 and score_home>score_away))')

    def getDraws(self, profileId):
        return self._count(profileId, 'AND score_home=score_away')

    @defer.inlineCallbacks
    def _goals(self, profileId, home):
        cols = 'score_home,score_away' if home else 'score_away,score_home'
        sql = 'SELECT sum(%s),sum(%s) ' % tuple(cols.split(',')) + _RANKED + \
              'AND home=%s'
        rows = yield self.dbController.dbRead(0, sql, profileId, int(home))
        defer.returnValue((int(rows[0][0] or 0), int(rows[0][1] or 0)))

    def getGoalsHome(self, profileId):
        return self._goals(profileId, True)

    def getGoalsAway(self, profileId):
        return self._goals(profileId, False)

    def _storeTxn(self, transaction, match):
        friendly = bool(match.friendly)
        if not friendly:
            # ranked: data6 stores it and updates the win streaks
            return super()._storeTxn(transaction, match)
        # friendly: store result + players, leave streaks alone
        transaction.execute(
            'INSERT INTO matches (score_home, score_away, team_id_home, '
            'team_id_away, friendly) VALUES (%s,%s,%s,%s,1)', (
                match.score_home, match.score_away,
                match.teamSelection.home_team_id,
                match.teamSelection.away_team_id))
        transaction.execute('SELECT LAST_INSERT_ID()')
        matchId = transaction.fetchall()[0][0]
        ts = match.teamSelection
        for home, players in ((1, [ts.home_captain] + ts.home_more_players),
                              (0, [ts.away_captain] + ts.away_more_players)):
            for profile in players:
                transaction.execute(
                    'INSERT INTO matches_played (match_id, profile_id, home) '
                    'VALUES (%s, %s, %s)', (matchId, profile.id, home))
        return matchId


class ProfileData(data6.ProfileData):

    @defer.inlineCallbacks
    def getSettings(self, profileId):
        """`settings` row as {column: value}, {} if none"""
        sql = 'SELECT %s FROM settings WHERE profile_id = %%s' % (
            ','.join(SETTINGS_COLUMNS))
        rows = yield self.dbController.dbRead(0, sql, profileId)
        defer.returnValue(dict(zip(SETTINGS_COLUMNS, rows[0])) if rows else {})

    @defer.inlineCallbacks
    def storeSettings(self, profileId, settings):
        sql = 'INSERT INTO settings (profile_id,%s) VALUES (%s) ' \
              'ON DUPLICATE KEY UPDATE %s' % (
                  ','.join(SETTINGS_COLUMNS),
                  ','.join(['%s'] * (len(SETTINGS_COLUMNS) + 1)),
                  ','.join('%s=VALUES(%s)' % (c, c) for c in SETTINGS_COLUMNS))
        yield self.dbController.dbWrite(
            0, sql, profileId, *[settings[c] for c in SETTINGS_COLUMNS])
        defer.returnValue(True)
