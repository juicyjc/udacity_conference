#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import StringMessage
from models import Session
from models import SessionForm
from models import SessionForms
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms
from models import TypeOfSession

from utils import getUserId

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

import logging

__author__ = 'wesc+api@google.com (Wesley Chun)'

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"

ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')

FEATURED_SPEAKER_TPL = ('Join Featured Speaker {} for the following sessions: {}')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
}

FIELDS = {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESH_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESH_GET_REQUEST_TYPE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.EnumField(TypeOfSession, 2),
)

SESH_GET_REQUEST_SPEAKER = endpoints.ResourceContainer(
    message_types.VoidMessage,
    email=messages.StringField(1),
)

SESH_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

WISHLIST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

WISHLIST_REQUEST_CONF = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1',
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                      'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        # if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        # else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()  # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

# - - - Sessions - - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, sesh):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sesh, field.name):
                # convert date and startTime to string; just copy others
                if field.name == 'date' or field.name == 'startTime':
                    setattr(sf, field.name, str(getattr(sesh, field.name)))
                else:
                    setattr(sf, field.name, getattr(sesh, field.name))
            elif field.name == "websafeConferenceKey":
                setattr(sf, field.name, sesh.key.urlsafe())
        # query speaker by the speaker_id and add values to form
        if sesh.speakerId:
            speaker = Speaker.get_by_id(sesh.speakerId)
            sf.speaker_name = speaker.name
            sf.speaker_email = speaker.email
            sf.speaker_gender = speaker.gender
        sf.check_initialized()
        return sf

    def _createSessionObject(self, request):
        """Create Session object, returning SessionForm/request."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        # check to see that the current user is the conference organizer
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        if conf_key.parent().get().mainEmail != user_id:
            raise endpoints.UnauthorizedException(
                'Only the organizer of the conference can create sessions')

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # copy SessionForm/ProtoRPC Message into dictionary
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        # delete the unneeded values
        del data['websafeConferenceKey']
        del data['speaker_name']
        del data['speaker_email']
        del data['speaker_gender']

        # Format date and startTime, ie. 2015-08-18 and 16:00
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:10], "%H:%M").time()

        # if speaker email was submitted, get or create a speaker
        if request.speaker_email:
            # check to see if this speaker exists
            speaker = Speaker.query(Speaker.email == request.speaker_email).get()
            # if so, use this speaker's values
            if speaker:
                # Update speaker from request
                speaker.name = request.speaker_name
                speaker.gender = request.speaker_gender
                speaker.put()
                speaker_key = speaker.key
            else:
                # create the speaker
                speaker_data = {
                    'name': request.speaker_name,
                    'email': request.speaker_email,
                    'gender': request.speaker_gender
                }
                speaker_key = Speaker(**speaker_data).put()
            data['speakerId'] = speaker_key.id()

        sesh_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        sesh_key = ndb.Key(Session, sesh_id, parent=conf_key)
        data['key'] = sesh_key

        Session(**data).put()

        # check to see if we should create a featured speaker
        if speaker:
            # get all sessions for this conference
            sessions = Session.query(ancestor=conf_key)
            speaker_sessions = []
            # loop over the sessions
            for session in sessions:
                # if the speaker of this session is the same as the speaker of
                # the session we just created
                if session.speakerId == data['speakerId']:
                    # add this session to our list
                    speaker_sessions.append(session)
            # create a comma-delimited list of session names
            session_names = ', '.join(session.name for session in speaker_sessions)
            # if this speaker is speaking in more than one session for this
            # conference then pass his name and session names to the task queue
            if(len(speaker_sessions) > 1):
                taskqueue.add(
                    params={'speaker_name': speaker.name,
                            'session_names': session_names},
                    url='/tasks/set_featured_speaker'
                )
        return self._copySessionToForm(sesh_key.get())

    @endpoints.method(SESH_POST_REQUEST, SessionForm,
                      path='session/{websafeConferenceKey}',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """Create a Session with a conference as its parent"""
        return self._createSessionObject(request)

    @endpoints.method(SESH_GET_REQUEST, SessionForms,
                      path='sessions/{websafeConferenceKey}',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Given a conference, return all sessions"""
        # websafeConferenceKey
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        # create ancestor query for all key matches for this conference
        sessions = Session.query(ancestor=conf_key).order(Session.name)

        # return set of SessionForm objects per Conference
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESH_GET_REQUEST_TYPE, SessionForms,
                      path='sessions/{websafeConferenceKey}/{typeOfSession}',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Given a conference, return all sessions of a specified type (eg lecture, keynote, workshop)"""
        # websafeConferenceKey
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        # create ancestor query for all key matches for this conference
        sessions = Session.query(
            Session.typeOfSession == request.typeOfSession,
            ancestor=conf_key
        ).order(Session.name)

        # return set of SessionForm objects per Conference
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESH_GET_REQUEST_SPEAKER, SessionForms,
                      path='sessions/speaker/{email}',
                      http_method='GET', name='getSesionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Given a speaker's email, return all sessions given by this particular speaker, across all conferences"""
        # get speaker by email
        speaker = Speaker.query(Speaker.email == request.email).get()
        if speaker:
            # get sessions by speakerID
            sessions = Session.query(Session.speakerId == speaker.key.id())
        else:
            raise endpoints.NotFoundException(
                'No speaker found with email address: %s' % request.email)

        # return set of SessionForm objects per speaker
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESH_GET_REQUEST, SessionForms,
                      path='sessions_i_like/{websafeConferenceKey}',
                      http_method='GET', name='getConferenceSessionsILike')
    def getConferenceSessionsILike(self, request):
        """Given a conference, return all sessions that aren't workshops and start before 7PM"""
        # websafeConferenceKey
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        # create ancestor query for all key matches for this conference
        sessions = Session.query(
            # we only want sessions that are not WORKSHOPs from this conference
            Session.typeOfSession != TypeOfSession.WORKSHOP,
            ancestor=conf_key
        )
        sessions_to_exclude = []
        # loop over the list of non-WORKSHOP sessions from this conference
        for session in sessions:
            # if this session starts after 7PM, add it to the exclude list
            if session.startTime > datetime.strptime('19:00', "%H:%M").time():
                sessions_to_exclude.append(session.key.id())
        # return set of SessionForm objects that are not in our excude list
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions if session.key.id() not in sessions_to_exclude]
        )

# - - - Wishlist - - - - - - - - - - - - - - - - - - - -
    def _sessionWishlist(self, request, add=True):
        """Add or remove a session from a user's wishlist."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # Check if session exists given websafeSessionKey
        wssk = request.websafeSessionKey
        sesh = ndb.Key(urlsafe=wssk).get()
        if not sesh:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wssk)

        # Add session to wishlit
        if add:
            # check if user already has this seession in their wishlist
            if wssk in prof.sessionKeysWishlist:
                raise ConflictException(
                    "You already have this session in your wishlist")
            # Add
            prof.sessionKeysWishlist.append(wssk)
            retval = True
        # Remove from wishlist
        else:
            # Check if session in wishlist
            if wssk in prof.sessionKeysWishlist:
                # Remove
                prof.sessionKeysWishlist.remove(wssk)
                retval = True
            else:
                retval = False

        # Write profile back to the datastore & return
        prof.put()
        return BooleanMessage(data=retval)

    @endpoints.method(WISHLIST_REQUEST, BooleanMessage,
                      path='wishlist/{websafeSessionKey}',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Given a session key, add it to a user's wishlist"""
        return self._sessionWishlist(request, add=True)

    @endpoints.method(WISHLIST_REQUEST, BooleanMessage,
                      path='wishlist/{websafeSessionKey}',
                      http_method='DELETE', name='removeSessionFromWishlist')
    def removeSessionFromWishlist(self, request):
        """Given a session key, remove it from a user's wishlist"""
        return self._sessionWishlist(request, add=False)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='wishlist',
                      http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get a list of sessions from the user's wishlist"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        prof = self._getProfileFromUser()  # get user Profile
        sesh_keys = [ndb.Key(urlsafe=wssk) for wssk in prof.sessionKeysWishlist]
        sessions = ndb.get_multi(sesh_keys)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(WISHLIST_REQUEST_CONF, SessionForms,
                      path='wishlist/{websafeConferenceKey}',
                      http_method='GET', name='getSessionsInWishlistPerConf')
    def getSessionsInWishlistPerConf(self, request):
        """Get a list of sessions from the user's wishlist for a conference."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        prof = self._getProfileFromUser()  # get user Profile
        # get the websafe conference key
        wsck = request.websafeConferenceKey
        conf_key = ndb.Key(urlsafe=wsck)
        sessions = []
        # loop over the websafe session keys from the user's profile
        for wssk in prof.sessionKeysWishlist:
            # get the session key
            sesh_key = ndb.Key(urlsafe=wssk)
            # get the parent conference key from the session key
            this_conf_key = sesh_key.parent()
            sesh = sesh_key.get()
            # if the session's parent matches the requested conference
            if conf_key.id() == this_conf_key.id():
                # add the session to our list of sessions
                sessions.append(sesh)

        # return set of SessionForm objects per conference
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

# - - - Speaker - - - - - - - - - - - - - - - - - - - -
    @staticmethod
    def _setFeaturedSpeaker(speaker_name, session_names):
        """Format the FeaturedSpeaker text string and pass to memcache."""
        featuredSpeaker = FEATURED_SPEAKER_TPL.format(speaker_name, session_names)
        memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, featuredSpeaker)
        return featuredSpeaker

    def _createSpeakerObject(self, request):
        """Create a Speaker object, returning SpeakerForm/request."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # name and email are required
        if not request.name:
            raise endpoints.BadRequestException("Speaker 'name' field required")
        if not request.email:
            raise endpoints.BadRequestException("Speaker 'email' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        Speaker(**data).put()
        return request

    def _copySpeakerToForm(self, speaker):
        """Copy fields from Speaker to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
        sf.check_initialized()
        return sf

    @endpoints.method(SpeakerForm, SpeakerForm,
                      path='createSpeaker',
                      http_method='POST', name='createSpeaker')
    def createSpeaker(self, request):
        """Create a new speaker"""
        return self._createSpeakerObject(request)

    @endpoints.method(message_types.VoidMessage, SpeakerForms,
                      path='speaker',
                      http_method='GET', name='getSpeakers')
    def getSpeakers(self, request):
        """Return all speakers."""
        # get all speakers
        speakers = Speaker.query().fetch()
        return SpeakerForms(
            items=[self._copySpeakerToForm(speaker) for speaker in speakers]
        )

    @endpoints.method(SPEAKER_GET_REQUEST, SpeakerForms,
                      path='speaker/{websafeConferenceKey}',
                      http_method='GET', name='getSpeakersByConf')
    def getSpeakersByConf(self, request):
        """Given a conference, return all speakers of the conference's sessions."""
        # websafeConferenceKey
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        # create a session ancestor query for all conference key matches for the requested conference
        sessions = Session.query(ancestor=conf_key).fetch()
        speakerIds = []
        # loop over the list of sessions from this conference
        for session in sessions:
            # if this speaker is not in our list, add him to it
            if session.speakerId not in speakerIds:
                speakerIds.append(session.speakerId)
        # get a list of keys from our speakerIds list
        keys = [ndb.Key(Speaker, this_id) for this_id in speakerIds]
        # use our keys to get our speakers
        speakers = ndb.get_multi(keys)

        # return set of SessionForm objects per Conference
        return SpeakerForms(
            items=[self._copySpeakerToForm(speaker) for speaker in speakers]
        )

    @endpoints.method(SpeakerForm, SpeakerForms,
                      path='speaker',
                      http_method='POST', name='getSpeaker')
    def getSpeaker(self, request):
        """Search for a speaker by email or name."""
        # make sure user entered one of the two optional search fields
        if not request.email and not request.name:
            raise endpoints.BadRequestException(
                "Please enter either 'email' or 'name' to search for a Speaker."
            )
        # query by email
        if request.email:
            speakers = Speaker.query(Speaker.email == request.email).fetch()
        # if no email, query by name
        else:
            speakers = Speaker.query(Speaker.name == request.name).fetch()

        # return set of SpeakerForm objects
        return SpeakerForms(
            items=[self._copySpeakerToForm(speaker) for speaker in speakers]
        )

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='speaker/featured/get',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Speaker from memcache."""
        featuredSpeaker = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)
        if not featuredSpeaker:
            featuredSpeaker = ""
        return StringMessage(data=featuredSpeaker)

# - - - Test - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='filterPlayground',
                      http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.filter(Conference.month == 6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

api = endpoints.api_server([ConferenceApi])  # register API
