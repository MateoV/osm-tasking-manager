import transaction
import json
import urllib

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import Unicode
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Table
from sqlalchemy import Column
from sqlalchemy import ForeignKey
from sqlalchemy import event

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base

from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import relationship

from zope.sqlalchemy import ZopeTransactionExtension

from pyramid.security import Allow
from pyramid.security import Everyone
from pyramid.security import Authenticated

from OSMTM.utils import TileBuilder
from OSMTM.utils import max 
from OSMTM.utils import get_tiles_in_geom
from shapely.wkt import loads

from OSMTM.history_meta import VersionedMeta, VersionedListener
from OSMTM.history_meta import _history_mapper 

from datetime import datetime

DBSession = scoped_session(sessionmaker(extension=[ZopeTransactionExtension(), VersionedListener()]))
Base = declarative_base()

class RootFactory(object):
    __acl__ = [ (Allow, Everyone, 'view'),
                (Allow, Authenticated, 'edit'),
                (Allow, Authenticated, 'job'),
                (Allow, 'group:admin', 'admin') ]
    def __init__(self, request):
        pass

class Tile(Base):
    __metaclass__ = VersionedMeta
    __tablename__ = "tiles"
    x = Column(Integer, primary_key=True)
    y = Column(Integer, primary_key=True)
    zoom = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('jobs.id'), primary_key=True, index=True)
    username = Column(Unicode, ForeignKey('users.username'), index=True)
    update = Column(DateTime)
    checkout = Column(Boolean, default=False)
    checkin = Column(Integer)
    change = Column(Boolean, default=False)
    comment = Column(Unicode)
    geometry = Column(Unicode)
    import_file = Column(Unicode)

    def __init__(self, x, y, zoom, geometry=None, import_file=None):
        self.x = x
        self.y = y
        self.zoom = zoom
        self.geometry = geometry
        self.import_file = import_file
        self.checkin = 0

    def to_polygon(self, srs=900913):
        if self.geometry:
            return loads(self.geometry)
        # tile size (in meters) at the required zoom level
        step = max/(2**(self.zoom - 1))
        tb = TileBuilder(step)
        return tb.create_square(self.x, self.y, srs)

def tile_before_update(mapper, connection, target):
    d = datetime.now()
    target.update = d
    target.job.done = target.job.get_percent_done()
    target.job.last_update = d

event.listen(Tile, 'before_update', tile_before_update)

TileHistory = Tile.__history_mapper__.class_

job_whitelist_table = Table('job_whitelists', Base.metadata,
    Column('job_id', Integer, ForeignKey('jobs.id')),
    Column('user_id', Unicode, ForeignKey('users.username'))
)

users_licenses_table = Table('users_licenses', Base.metadata,
    Column('user', Integer, ForeignKey('users.username')),
    Column('license', Integer, ForeignKey('licenses.id'))
)

class License(Base):
    __tablename__ = "licenses"
    id = Column(Integer, primary_key=True)
    name = Column(Unicode)
    description = Column(Unicode)
    plain_text = Column(Unicode)
    jobs = relationship("Job", backref='license')

    def __init__(self):
        pass

class User(Base):
    __tablename__ = "users"
    username = Column(Unicode, primary_key=True)
    admin = Column(Boolean)
    task = relationship(Tile, backref='user')
    accepted_licenses = relationship(License, secondary=users_licenses_table)

    def __init__(self, username, admin=False):
        self.username = username
        self.admin = admin

    def is_admin(self):
        return self.admin == True

job_tags_table = Table('job_tags', Base.metadata,
    Column('job_id', Integer, ForeignKey('jobs.id')),
    Column('tag', Integer, ForeignKey('tags.tag'))
)

class Tag(Base):
    __tablename__ = "tags"
    tag = Column(Unicode, primary_key=True)

    def __init__(self, tag):
        self.tag = tag

class Job(Base):
    """ The SQLAlchemy declarative model class for a Page object. """
    __tablename__ = 'jobs'
    id = Column(Integer, primary_key=True)
    title = Column(Unicode, unique=True)
    # statuses are:
    # 0 - archived 
    # 1 - published
    # 2 - draft
    status = Column(Integer)
    description = Column(Unicode)
    short_description = Column(Unicode)
    task_extra = Column(Unicode)
    geometry = Column(Unicode)
    workflow = Column(Unicode)
    imagery = Column(Unicode)
    zoom = Column(Integer)
    josm_preset = Column(Unicode)
    is_private = Column(Boolean)
    featured = Column(Boolean)
    # percentage done
    done = Column(Integer)
    last_update = Column(DateTime)
    author = Column(Unicode, ForeignKey('users.username'))
    tiles = relationship(Tile, backref='job', cascade="all, delete, delete-orphan")
    users = relationship(User,
                secondary=job_whitelist_table,
                backref='private_jobs')
    tags = relationship(Tag, secondary=job_tags_table, backref='tags')
    license_id = Column(Integer, ForeignKey('licenses.id'))

    def __init__(self, title=None,
                 geometry=None, zoom=None, geojson_url=None, author=None):
        self.title = title
        self.status = 1
        self.geometry = geometry
        self.short_description = u''
        self.description = u''
        self.workflow = u''
        self.zoom = zoom
        self.geojson_url = geojson_url
        self.author = author

        tiles = []
        
        if geojson_url:
            def build_wkt(polygon):
                wkt = ''
                for c in polygon:
                    if wkt != '':
                        wkt = wkt + ','
                    wkt = wkt + str(c[0]) + " " + str(c[1])
                return '(' + wkt + ')'
                
            jsonurl = urllib.urlopen(geojson_url)
            data = json.loads(jsonurl.read())
            
            x = 0
            for f in data['features']:
                wkt_string = []
                try:
                    import_url = f['properties']['import_url']
                except KeyError:
                    import_url = None
                for p in f['geometry']['coordinates']:
                    if f['geometry']['type'] == "MultiPolygon":
                        wkt_string = []
                        for subp in p:
                            wkt_string.append(build_wkt(subp))
                        t = Tile(x,0,0,'POLYGON(' + ','.join(wkt_string) + ')',import_url)
                        x = x + 1
                        tiles.append(t)
                    else:
                        wkt_string.append(build_wkt(p))
                if f['geometry']['type'] != "MultiPolygon":
                    t = Tile(x,0,0,'POLYGON(' + ','.join(wkt_string) + ')',import_url)
                    x = x + 1
                    tiles.append(t)
        else:
            for i in get_tiles_in_geom(loads(geometry), int(zoom)):
                tiles.append(Tile(i[0], i[1], int(zoom)))
          
        self.tiles = tiles

    def get_last_update(self):
        updates = []
        for tile in self.tiles:
            if tile.update is not None:
                updates.append(tile.update)
        updates.sort(reverse=True)
        return updates[0] if len(updates) > 0 else None

    def get_percent_done(self):
        total = 0
        done = 0
        for tile in self.tiles:
            area = 1.0/(1 + tile.zoom - self.zoom)**2
            total = total + area
            if tile.checkin > 0:
                done = done + area
        return round(done * 100 / total)

    def get_centroid(self):
        geom = loads(self.geometry)
        return geom.centroid

def group_membership(username, request):
    session = DBSession()
    user = session.query(User).get(username)
    perms = []
    if user:
        for job in user.private_jobs:
            perms += ['job:'+str(job.id)]
        if user.is_admin():
            perms += ['group:admin']
    return perms

def populate(admin_user):
    transaction.begin()
    session = DBSession()
    user = User(u'foo')
    session.add(user)
    user = User(admin_user, admin=True)
    session.add(user)
    job = Job(u'SomeTitle', 'MULTIPOLYGON(((6289.9074513149 5661386.6698623,6292.0781813851 5661385.1043913,6293.0355290058 5661314.2473508,6291.9000701999 5661298.3239642,6295.5179536501 5661231.0570599,6295.2507868723 5661187.4935485,6286.055796934 5661104.8736761,6286.4565471008 5661070.3395854,6295.7294606826 5660900.2801001,6300.8946850547 5660776.4256928,6303.1210748703 5660774.5757165,6302.2527828422 5660772.2513879,6306.4272637464 5660731.4729581,6320.4646515334 5660684.9392879,6323.2031110066 5660667.4200481,6323.982347442 5660638.7853353,6313.4626555635 5660525.0853972,6310.4681612616 5660516.6579664,6310.3123139745 5660498.8228293,6307.4959308578 5660468.3703633,6308.2195075479 5660445.4915535,6324.8840353173 5660345.8658474,6324.8840353173 5660345.8658474,6318.6390118847 5660328.2365921,6242.8638345122 5660211.1570799,6212.6962525115 5660175.8833221,6133.915448888 5660122.7120701,6110.0930778616 5660097.6680925,6079.81417637 5660055.7069381,6070.7416378716 5660036.0070817,6027.0932655377 5659964.7812359,5970.888054644 5659861.4929051,5961.1142033537 5659838.4101097,5936.401276401 5659752.9726627,5908.916494128 5659681.4170546,5900.6677198614 5659643.8687699,5903.3059917928 5659590.1315187,5900.9794144355 5659568.6620037,5891.8734800899 5659535.9677303,5873.9176462275 5659505.0442428,5858.2104660787 5659486.2309257,5839.5755833225 5659469.1883078,5829.5011694071 5659462.8012867,5775.4221607873 5659443.719298,5763.0323014637 5659436.5734435,5751.6777134044 5659425.3803909,5743.0281889709 5659412.0688973,5723.0352084273 5659370.838173,5702.9754361891 5659314.4466107,5673.8876532489 5659176.0698252,5622.3244651206 5658950.2892711,5611.2481757882 5658913.676743,5592.4574457449 5658865.7139246,5563.5032461936 5658775.9226526,5536.5416655272 5658614.4270882,5499.5056709455 5658597.1173894,5483.9432061347 5658586.6683545,5427.7045993938 5658521.6032305,5408.3016121512 5658512.6085991,5374.3714313622 5658508.5934238,5370.5865686757 5658509.9212768,5364.0855104143 5658507.471072,5361.035356367 5658508.4511538,5359.8776336629 5658506.6174524,5312.3442111008 5658500.8634261,5288.7110832087 5658493.3389357,5198.3530525444 5658437.9961865,5170.8348744241 5658415.1857781,5130.3257117301 5658370.1974362,5127.6206481042 5658370.3713191,5096.0615724687 5658333.8559992,5061.0849884663 5658285.4852846,5049.2183307494 5658272.4125904,4987.7811037891 5658230.4757688,4948.4630596464 5658197.5650643,4929.0155446075 5658175.8617695,4904.9148748541 5658138.5726912,4861.1440510803 5658046.7970095,4858.1161609312 5658045.6431015,4732.8817338062 5658063.8369292,4699.0072127625 5658074.0956719,4630.3676147489 5658100.4459938,4628.4974473038 5658102.8012453,4615.6623100172 5658105.551674,4545.085752864 5658137.1342432,4505.723180925 5658148.9579764,4409.1535226753 5658144.6110139,4381.4127055735 5658140.8805313,4372.4960143622 5658141.7341162,4349.7311784981 5658150.9654834,4341.5380639769 5658156.86155,4312.1274545134 5658192.032537,4278.9876421088 5658225.4174394,4277.2399261036 5658223.5679884,4271.5960279212 5658232.4358722,4239.3912992392 5658247.4211929,4220.1552912328 5658250.2823171,4204.7932015055 5658249.2390342,4138.7918754233 5658216.0279231,4123.1848828163 5658212.1551456,4111.3961487429 5658211.9812658,4088.2639585593 5658219.0471099,4077.5327596483 5658222.3192141,4017.8989084386 5658228.2785566,3991.0041194667 5658228.0098328,3964.7549835413 5658222.7618176,3926.8173010843 5658206.4803441,3773.0516884729 5658299.9648568,3773.0516884729 5658299.9648568,3792.1429811413 5658364.8861079,3781.5342336702 5658369.7232104,3688.8830214958 5658398.9513786,3676.8048567465 5658401.5122069,3663.9251916635 5658334.6779875,3662.3889826907 5658331.7219916,3659.4946759305 5658330.8367738,3650.9008112425 5658334.0140739,3644.0769264578 5658343.6882482,3633.7130818664 5658353.4098555,3624.0171542196 5658359.2586324,3607.6309251771 5658363.4634312,3558.9954396563 5658366.6407428,3555.7894383219 5658367.099161,3555.7894383219 5658367.099161,3538.8354798765 5658376.0936467,3530.8204765405 5658382.3218196,3524.2192307374 5658390.1149456,3519.5104162775 5658403.9939981,3514.3674558035 5658468.7737316,3515.8814008781 5658498.9823029,3520.156069324 5658510.1267779,3537.7000210705 5658541.1733033,3546.0044550826 5658552.0649051,3607.0743277233 5658607.2660855,3635.4607978716 5658638.1706745,3646.5816150003 5658655.1484838,3670.4262499249 5658697.4824906,3679.7882190993 5658723.6291352,3680.3448165532 5658731.6596797,3672.1405700829 5658760.1618476,3670.4819096703 5658774.6421849,3672.3298132172 5658783.3367219,3679.2316216454 5658797.5483558,3722.8354661831 5658859.2957159,3731.5740462091 5658875.515139,3737.5964306602 5658892.746332,3740.3682859806 5658909.9775581,3747.7487682191 5659012.9071575,3759.6599537324 5659120.7702963,3759.3705230563 5659138.4129907,3757.7341265419 5659147.7876623,3754.0383194481 5659158.0476429,3748.594796349 5659167.0271117,3735.2364574557 5659181.0654541,3727.8559752171 5659193.6335834,3722.824334234 5659211.8771632,3721.5218961919 5659223.4809846,3721.5218961919 5659223.4809846,3731.7298934962 5659286.3537217,3729.5702953751 5659307.9806537,3700.3155331987 5659363.4078095,3686.5453121895 5659394.3308094,3681.7363101879 5659410.5511985,3680.5117957894 5659427.625324,3686.4339926987 5659466.1528912,3693.0686343491 5659484.3495962,3712.7721842168 5659515.7789179,3749.6968593077 5659567.254946,3753.448326147 5659577.5628347,3760.3501345752 5659625.719171,3761.2184266032 5659652.7064362,3759.2369396674 5659666.4293415,3749.9528941365 5659696.4522224,3745.010308746 5659750.3165893,3736.0156938912 5659770.9011788,3710.4456068595 5659804.5607205,3707.228473576 5659821.5565445,3708.6310991598 5659858.0779124,3717.9485405379 5659931.3740582,3716.8464775792 5659961.2081007,3700.5381721803 5660108.0240217,3700.5270402312 5660122.8701762,3703.2543677553 5660134.4751745,3756.0420702821 5660191.3462074,3767.6638251193 5660207.9316835,3776.5248565852 5660225.8611077,3781.7012129064 5660245.7194888,3782.1464908695 5660262.9533106,3774.1092236353 5660297.9111919,3755.6301881662 5660351.1309142,3754.4168057167 5660382.3735362,3768.1313669805 5660428.9056388,3783.1594982355 5660537.5289164,3777.1593776826 5660571.2703883,3768.3094781658 5660600.4899096,3770.7473750138 5660626.1677493,3779.4414272436 5660652.3041972,3793.8461693502 5660753.4037928,3776.4358009926 5660792.7592606,3771.7381184817 5660811.1009598,3771.9718894124 5660841.2699742,3774.5433696493 5660862.5211646,3801.9947560751 5660958.95856,3807.5829945122 5660991.689638,3808.4846823875 5661057.0730904,3806.1692369793 5661089.2352825,3809.0746756886 5661111.9892333,3817.1342068209 5661143.2186898,3910.8429541577 5661318.7856002,3922.0194310317 5661344.845,3924.780154403 5661359.2346282,3918.7911657992 5661410.9426032,3918.4238114796 5661436.670199,3921.7077364576 5661444.2445943,3936.9807705923 5661458.7134466,3964.7883793886 5661462.4136814,3989.467910494 5661470.8736257,4001.3902279563 5661478.3215459,4009.3495715469 5661488.3786199,4024.9343002558 5661517.3481161,4040.5190289647 5661554.8567948,4051.7066377879 5661574.7181148,4069.8405828356 5661592.8716527,4108.5797656262 5661651.760211,4142.1537240448 5661696.0692037,4147.6417749401 5661709.8110449,4150.1575954317 5661752.1436384,4156.1020562393 5661764.3832824,4173.8352511202 5661776.7336378,4215.2349697404 5661791.8355679,4225.6544740772 5661798.9042287,4232.1666642877 5661823.7473738,4234.6490889321 5661825.8031432,4242.6863561662 5661824.791072,4249.0093032424 5661821.0748743,4265.562511521 5661800.3590768,4278.8317948218 5661789.8905011,4363.5236634055 5661765.0158226,4371.5720625887 5661766.1385816,4432.8534422619 5661750.6097307,4548.659108518 5661712.9737271,4633.2730534582 5661670.6729449,4744.5146205924 5661645.5772105,4797.6808093879 5661625.6682941,4837.9450692022 5661601.7428635,4903.7014924047 5661572.2512606,4949.5651226051 5661558.3673116,5019.7965893368 5661557.8612911,5099.1896501595 5661545.3056662,5119.1937626523 5661543.9773643,5151.3094357417 5661547.6143818,5260.9146063615 5661574.1172143,5323.8769103454 5661583.5893077,5371.9224025651 5661584.0320769,5422.3501318874 5661577.5961122,5502.5335610946 5661558.6519481,5542.3970707422 5661546.1912008,5586.7356239189 5661528.6702809,5627.1668629694 5661506.1840962,5645.5345789478 5661498.4198921,5700.6822546791 5661494.0871213,5873.5057641116 5661466.4301773,5942.1119662779 5661450.2851397,6052.908255449 5661414.8800117,6099.5956498812 5661405.3764705,6233.3014902544 5661386.0531615,6280.5454821405 5661385.1043913,6289.9074513149 5661386.6698623)))', 16)
    job.task_extra = u'<p>You can use the following <a href="http://www.osmsharp.com:815/data/{x}/{y}/{z}/WRI_RDC_localities_2009.osm" target="_blank" rel="tooltip" data-original-title="Right-click on the link to save the file (JOSM) or copy its location (Potlatch)."> .osm</a> file to import data.</p><p>See <a href="http://wiki.openstreetmap.org/wiki/Central_Africa_Import">Central Africa Import</a>'
    session.add(job)
    session.flush()
    transaction.commit()

def initialize_sql(engine, admin_user=u'admin_user'):
    Base.metadata.bind = engine
    Base.metadata.create_all(engine)
    try:
        populate(admin_user)
    except IntegrityError:
        transaction.abort()
