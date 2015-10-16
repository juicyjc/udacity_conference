# udacity_conference
Project Four: Conference Central

This is the repository for Project Four of the Udacity Fullstack Web Developer Nanodegree - Conference Central.

## Quick start

To run this application:

1. Clone this repository - https://github.com/juicyjc/udacity_catalog.git
2. Make sure that you are running Python 2.7.6.
3. Download and install the latest version of Google App Engine for Python. (https://cloud.google.com/appengine/downloads?hl=en)
4. Start up Google App Engine and click on File > Add Existing Application. Point to the repo that you've just downloaded.
5. Select the repo and press the Run button.
6. Take a look at the port number next to the application and browse to localhost:port_number.
7. Look at the admin port number and browse to localhost:admin_port_number/datastore to see the local DataStore interface.
8. Enjoy!

## Design Choices for Speaker and Session

In order to add sessions to Conference Central, I created a Session class in models.py with the following properties: name, highlights, speakerId, duration, typeOfSession, date, and startTime. SpeakerId is a reference to an entity of the Speaker class which I will discuss in the next paragraph. TypeOfSession is an EnumProperty which references an Enum class that currently has seven options. Each session is created as a child of a particular conference. I also have a SessionForm which has the same properties as the Session class except that instead of a speakerId it accepts the three properties of the Speaker class. It also has a websafeKey property so that the key can be viewed from APIs Explorer. There is also a SessionForms class to accommodate multiple SessionForm entities.

The Speaker is a class of its own with the following properties: name, email, and gender. This could easily be extended to include more properties but this seemed adequate for the purposes of this application. When a user creates a Session, he is asked to provide speaker_email, speaker_name, and speaker_gender. The application checks to see if there exists a Speaker entity with that email address. If not then it creates a new Speaker entity and it adds that speakerId as a property of the session entity. If that email address is in use then it updates the Speaker entity with the two other properties provided.

The properties of the Session and Speaker kinds are mostly String and Integer properties as would be expected. I chose to make duration an IntegerProperty which is meant to be a number of minutes as this seemed to be the easiest approach. I decided to make typeOfSession an msgprop.EnumProperty and to allow multiple entries so that a Session could be, for instance, both a WORKSHOP and a LUNCH, or a KEYNOTE and a PARTY. I wasn't sure the best approach to take but this seems to be working as expected. A list of Longs is stored in the typeOfSession property in Session, the values pulled from the TypeOfSession Enum.

## New Queries

1. removeSessionFromWishlist() - Given a sessionId, remove the session from a user's wishlist. This seemed like an easy win - I created one function for updating a user's wishlist and passed in a flag for add or remove depending on the endpoint.
2. getSessionsInWishlist() - Get ALL sessions in a user's wishlist (instead of just for a particular conference.)
3. getSpeakers() - Get all speakers.
4. getSpeakersByConf() - Given a conference, return all of the speakers that are speaking at that conference's sessions.
5. getSpeaker() - Search for a speaker by email or name.

## Query for All Non-Workshop Sessions Before 7PM

#### The Problem
"BadRequestError: Only one inequality filter per query is supported. Encountered both typeOfSession and startTime"

#### My Soultion
getConferenceSessionsILike() method

#### Discussion
When I first read this problem I seemed to recall from the lesson that in DataStore I could only query for one inequality at a time. I tried a query for both inequalities to see what would happen and that resulted in the error listed above. My next thought was that I'd have to do the second filter programmatically after getting the result set back from the first. I decided to Google the issue just in case there was some way to get around this issue in one DS query. However, I found a StackOverflow posting that confirmed my suspicions:

http://stackoverflow.com/questions/14205571/ndb-query-excluding-multiple-keys-or-ids

Thus, I took the following approach to solve the problem:

Query DataStore for one of the desired characteristics and then loop through the result set and create a list of sessionIds that have the other characteristic that we want to exclude. Then in our return when we create our SessionForms object using a List Comprehension, we can exclude the sessions that have IDs in our exclude list. I chose to have DataStore handle the typeOfSession exclusion because it seemed harder to do manually because of the way it was implemented. TypeOfSession is an enum list but DataStore knows what I mean when I write "Session.typeOfSession != TypeOfSession.WORKSHOP" as part of the query. Then I can simply loop through the result set and add the IDs of sessions that have a startTime greater than 7PM to our exclusion list.

## Classes

#### SessionForm(messages.Message)
+ name - string
+ highlights - string
+ speaker_name - string
+ speaker_email - string
+ speaker_gender - string
+ duration - integer (minutes)
+ typeOfSession - enum
+ date - string > date (ie. '2015-10-13')
+ startTime - string > time (ie. '18:00')
+ websafeKey - string

#### TypeOfSession(messages.Enum)
+ NOT_SPECIFIED - 1
+ WORKSHOP - 2
+ LECTURE - 3
+ KEYNOTE - 4
+ LUNCH - 5
+ DINNER - 6
+ PARTY - 7

#### SpeakerForm(messages.Message)
+ name
+ email
+ gender


## Endpoints

#### createSession()
POST - session/{websafeConferenceKey}

Request
+ websafeConferenceKey
+ SessionForm

Response - SessionForm

#### getConferenceSessions()
GET - sessions/{websafeConferenceKey}

Request - websafeConferenceKey

Response - SessionForms

#### getConferenceSessionsByType()
GET - sessions/{websafeConferenceKey}/{typeOfSession}

Request
+ websafeConferenceKey
+ typeOfSession

Response - SessionForms

#### getSessionsBySpeaker()
GET - sessions/speaker/{email}

Request - email

Response - SessionForms

#### getConferenceSessionsILike()
GET - sessions_i_like/{websafeConferenceKey}

Request - websafeConferenceKey

Response - SessionForms

#### addSessionToWishlist()
POST - wishlist/{websafeSessionKey}

Request - websafeSessionKey

Response - BooleanMessage

#### removeSessionFromWishlist()
DELETE - wishlist/{websafeSessionKey}

Request - websafeSessionKey

Response - BooleanMessage

#### getSessionsInWishlist()
GET - wishlist

Response - SessionForms

#### getSessionsInWishlistPerConf()
GET - wishlist/{websafeConferenceKey}

Request - websafeConferenceKey

Response - SessionForms

#### createSpeaker()
POST - createSpeaker

Request - SpeakerForm

Response - SpeakerForm

#### getSpeakers()
GET - speaker

Response - SpeakerForms

#### getSpeakersByConf()
GET - speaker/{websafeConferenceKey}

Request - websafeConferenceKey

Response - SpeakerForms

#### getSpeaker()
POST - speaker

Request
+ email
+ name

Response - SpeakerForms

#### getFeaturedSpeaker()
GET - speaker/featured/get

Response - StringMessage
