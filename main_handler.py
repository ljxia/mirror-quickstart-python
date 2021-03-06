# Copyright (C) 2013 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Request Handler for /main endpoint."""

__author__ = 'alainv@google.com (Alain Vongsouvanh)'


import io
import jinja2
import logging
import os
import webapp2
import urllib
import json
from datetime import date, datetime
import time
from google.appengine.api import memcache
from google.appengine.api import urlfetch
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers
from google.appengine.ext import db

import httplib2
from apiclient import errors
from apiclient.http import MediaIoBaseUpload
from apiclient.http import BatchHttpRequest
from oauth2client.appengine import StorageByKeyName

from model import Credentials
from model import JournalystEntry
import util

jinja_environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)))


PAGINATED_HTML = """
<article class='auto-paginate'>
<h2 class='blue text-large'>Did you know...?</h2>
<p>Cats are <em class='yellow'>solar-powered.</em> The time they spend
napping in direct sunlight is necessary to regenerate their internal
batteries. Cats that do not receive sufficient charge may exhibit the
following symptoms: lethargy, irritability, and disdainful glares. Cats
will reactivate on their own automatically after a complete charge
cycle; it is recommended that they be left undisturbed during this
process to maximize your enjoyment of your cat.</p><br/><p>
For more cat maintenance tips, tap to view the website!</p>
</article>
"""


class _BatchCallback(object):
  """Class used to track batch request responses."""

  def __init__(self):
    """Initialize a new _BatchCallback object."""
    self.success = 0
    self.failure = 0

  def callback(self, request_id, response, exception):
    """Method called on each HTTP Response from a batch request.

    For more information, see
      https://developers.google.com/api-client-library/python/guide/batch
    """
    if exception is None:
      self.success += 1
    else:
      self.failure += 1
      logging.error(
          'Failed to insert item for user %s: %s', request_id, exception)


class MainHandler(webapp2.RequestHandler):
  """Request Handler for the main endpoint."""

  def _render_template(self, message=None):
    """Render the main page template."""
    template_values = {'userId': self.userid}
    if message:
      template_values['message'] = message
    # self.mirror_service is initialized in util.auth_required.
    try:
      template_values['contact'] = self.mirror_service.contacts().get(
        id='python-quick-start').execute()
    except errors.HttpError:
      logging.info('Unable to find Python Quick Start contact.')

    timeline_items = self.mirror_service.timeline().list(maxResults=3).execute()
    template_values['timelineItems'] = timeline_items.get('items', [])

    subscriptions = self.mirror_service.subscriptions().list().execute()
    for subscription in subscriptions.get('items', []):
      collection = subscription.get('collection')
      if collection == 'timeline':
        template_values['timelineSubscriptionExists'] = True
      elif collection == 'locations':
        template_values['locationSubscriptionExists'] = True

    template = jinja_environment.get_template('templates/index.html')
    self.response.out.write(template.render(template_values))

  @util.auth_required
  def get(self):
    """Render the main page."""
    # Get the flash message and delete it.
    message = memcache.get(key=self.userid)
    memcache.delete(key=self.userid)
    self._render_template(message)

  @util.auth_required
  def post(self):
    """Execute the request and render the template."""
    operation = self.request.get('operation')
    # Dict of operations to easily map keys to methods.
    operations = {
        'insertSubscription': self._insert_subscription,
        'deleteSubscription': self._delete_subscription,
        'insertItem': self._insert_item,
        'insertPaginatedItem': self._insert_paginated_item,
        'insertItemWithAction': self._insert_item_with_action,
        'insertItemAllUsers': self._insert_item_all_users,
        'insertContact': self._insert_contact,
        'deleteContact': self._delete_contact,
        'deleteTimelineItem': self._delete_timeline_item
    }
    if operation in operations:
      message = operations[operation]()
    else:
      message = "I don't know how to " + operation
    # Store the flash message for 5 seconds.
    memcache.set(key=self.userid, value=message, time=5)
    self.redirect('/')

  def _insert_subscription(self):
    """Subscribe the app."""
    # self.userid is initialized in util.auth_required.
    body = {
        'collection': self.request.get('collection', 'timeline'),
        'userToken': self.userid,
        'callbackUrl': util.get_full_url(self, '/notify')
    }
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.subscriptions().insert(body=body).execute()
    return 'Application is now subscribed to updates.'

  def _delete_subscription(self):
    """Unsubscribe from notifications."""
    collection = self.request.get('subscriptionId')
    self.mirror_service.subscriptions().delete(id=collection).execute()
    return 'Application has been unsubscribed.'

  def _insert_item(self):
    """Insert a timeline item."""
    logging.info('Inserting timeline item')
    body = {
        'notification': {'level': 'DEFAULT'}
    }
    if self.request.get('html') == 'on':
      body['html'] = [self.request.get('message')]
    else:
      body['text'] = self.request.get('message')

    media_link = self.request.get('imageUrl')
    if media_link:
      if media_link.startswith('/'):
        media_link = util.get_full_url(self, media_link)
      resp = urlfetch.fetch(media_link, deadline=20)
      media = MediaIoBaseUpload(
          io.BytesIO(resp.content), mimetype='image/jpeg', resumable=True)
    else:
      media = None

    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body, media_body=media).execute()
    return  'A timeline item has been inserted.'

  def _insert_paginated_item(self):
    """Insert a paginated timeline item."""
    logging.info('Inserting paginated timeline item')
    body = {
        'html': PAGINATED_HTML,
        'notification': {'level': 'DEFAULT'},
        'menuItems': [{
            'action': 'OPEN_URI',
            'payload': 'https://www.google.com/search?q=cat+maintenance+tips'
        }]
    }
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body).execute()
    return  'A timeline item has been inserted.'

  def _insert_item_with_action(self):
    """Insert a timeline item user can reply to."""
    logging.info('Inserting timeline item')
    body = {
        'creator': {
            'displayName': 'Python Starter Project',
            'id': 'PYTHON_STARTER_PROJECT'
        },
        'text': 'Tell me what you had for lunch :)',
        'notification': {'level': 'DEFAULT'},
        'menuItems': [{'action': 'REPLY'}]
    }
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body).execute()
    return 'A timeline item with action has been inserted.'

  def _insert_item_all_users(self):
    """Insert a timeline item to all authorized users."""
    logging.info('Inserting timeline item to all users')
    users = Credentials.all()
    total_users = users.count()

    if total_users > 10:
      return 'Total user count is %d. Aborting broadcast to save your quota' % (
          total_users)
    body = {
        'text': 'Hello Everyone!',
        'notification': {'level': 'DEFAULT'}
    }

    batch_responses = _BatchCallback()
    batch = BatchHttpRequest(callback=batch_responses.callback)
    for user in users:
      creds = StorageByKeyName(
          Credentials, user.key().name(), 'credentials').get()
      mirror_service = util.create_service('mirror', 'v1', creds)
      batch.add(
          mirror_service.timeline().insert(body=body),
          request_id=user.key().name())

    batch.execute(httplib2.Http())
    return 'Successfully sent cards to %d users (%d failed).' % (
        batch_responses.success, batch_responses.failure)

  def _insert_contact(self):
    """Insert a new Contact."""
    logging.info('Inserting contact')
    id = self.request.get('id')
    name = self.request.get('name')
    image_url = self.request.get('imageUrl')
    if not name or not image_url:
      return 'Must specify imageUrl and name to insert contact'
    else:
      if image_url.startswith('/'):
        image_url = util.get_full_url(self, image_url)
      body = {
          'id': id,
          'displayName': name,
          'imageUrls': [image_url],
          'acceptCommands': [{ 'type': 'TAKE_A_NOTE' }]
      }
      # self.mirror_service is initialized in util.auth_required.
      self.mirror_service.contacts().insert(body=body).execute()
      return 'Inserted contact: ' + name

  def _delete_contact(self):
    """Delete a Contact."""
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.contacts().delete(
        id=self.request.get('id')).execute()
    return 'Contact has been deleted.'

  def _delete_timeline_item(self):
    """Delete a Timeline Item."""
    logging.info('Deleting timeline item')
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().delete(id=self.request.get('itemId')).execute()
    return 'A timeline item has been deleted.'
	
class JournalHandler(webapp2.RequestHandler):
  """Request Handler for the journal endpoint."""

  def _render_template(self, message=None, journals=[]):
    """Render the main page template."""
    upload_url = blobstore.create_upload_url('/upload')
    template_values = {'userId': self.userid, 'upload_url': upload_url}
    if message:
      template_values['message'] = message
    if journals:
      template_values['journals'] = journals
    template = jinja_environment.get_template('templates/journal.html')
    self.response.out.write(template.render(template_values))

  def _send_prompt(self):
    """Insert a timeline item."""
    logging.info('Inserting timeline item')
    body = {
        'creator': {
            'displayName': 'Schema Glass Journal',
            'id': 'SCHEMA_GLASS_JOURNAL'
        },
        'text': 'Time to record!',
        'notification': {'level': 'DEFAULT'},
        'menuItems': [{
          'action': 'OPEN_URI',
          'payload': 'com.schemadesign.glassjournal://open/' + str(self.userid),
          'values': [
            {
              'displayName': 'Record',
              'state': "DEFAULT"
            },
            {
              'displayName': 'Launching',
              'state': "PENDING"
            },
            {
              'displayName': 'Launched',
              'state': "CONFIRMED"
            }
          ]
        }]
    }


        # [{
        #   'action': 'OPEN_URI',
        #   'payload': 'com.schemadesign.journal://open',
          
        # }]

    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body).execute()
    return 'A timeline item with action has been inserted.'

  @util.auth_required
  def get(self, format):
    message_key = str(self.userid) + "_journal"
    message = memcache.get(key=message_key)
    memcache.delete(key=message_key)

    journals = JournalystEntry.all().filter("userId =", str(self.userid)).order("-created")

    def transform(journal):
      video_key = str(journal.video.key())
      # logging.info(video_key)
      return {
        'key': journal.key().id(),
        'created': journal.created,
        'category': journal.category,
        'emotion': journal.emotion,
        'location': journal.location,
        'video_key': str(video_key)
      }

    def json_transform(journal):
      video_key = str(journal.video.key())
      ms = time.mktime(journal.created.utctimetuple())
      ms += getattr(journal.created, 'microseconds', 0) / 1000
      return {
        'created': int(ms * 1000),
        'category': journal.category,
        'emotion': journal.emotion,
        'location': {
          'lat': journal.location and journal.location.lat,
          'lon': journal.location and journal.location.lon
        },
        'video_url': '/serve/' + str(video_key)
      }

    if format == '.json':
      journals = map(json_transform, journals)
      self.response.headers.add_header("Access-Control-Allow-Origin", "*")
      self.response.headers["Content-Type"] = "application/json"
      self.response.out.write( json.dumps(journals) )
    else:
      journals = map(transform, journals)
      self._render_template(message, journals)

  @util.auth_required
  def post(self, format=''):
    """Execute the request and render the template."""
    message_key = str(self.userid) + "_journal"

    operation = self.request.get('operation')
    # Dict of operations to easily map keys to methods.
    operations = {
      'sendPrompt': self._send_prompt
    }
    if operation in operations:
      message = operations[operation]()
    else:
      message = operation + " is not yet implemented"
    # Store the flash message for 5 seconds.
    memcache.set(key=message_key, value=message, time=5)
    self.redirect('/journal')

class JournalDeleteHandler(webapp2.RequestHandler):
  @util.auth_required
  def get(self, journal_key):
    journal = JournalystEntry.getByKey(journal_key)

    if journal:
      journal.delete()
      message_key = str(self.userid) + "_journal"
      message="Journal deleted"
      memcache.set(key=message_key, value=message, time=5)
      self.redirect('/journal')
      # self.response.out.write(journal.category + ": " + journal.emotion)
    else:
      self.response.out.write("not found")

class RequestUploadHandler(webapp2.RequestHandler):
  def get(self):
    upload_url = blobstore.create_upload_url('/upload')
    if self.request.get("form"):
      self.response.out.write('<html><body>')
      self.response.out.write('<form action="%s" method="POST" enctype="multipart/form-data">' % upload_url)
      self.response.out.write("""Upload File: <input type="file" name="file"><input type="hidden" name="userId" value="106814318928238419891"><input type="hidden" name="category" value="test"><input type="hidden" name="emotion" value="testemotion"><br> <input type="submit"
          name="submit" value="Submit"> </form></body></html>""")
    else:
      self.response.out.write(upload_url)

class UploadHandler(blobstore_handlers.BlobstoreUploadHandler):
  def post(self):
    upload_files = self.get_uploads('file')  # 'file' is file upload field in the form
    blob_info = upload_files[0]
    userId = self.request.get("userId")
    lat = self.request.get("lat")
    lon = self.request.get("lon")
    location = db.GeoPt(lat, lon)
    journal = JournalystEntry(video=blob_info, userId=self.request.get("userId"), category=self.request.get("category"), emotion=self.request.get("emotion"), location=location)
    journal.put()

    if self.request.get("html"):
      self.redirect('/journal')
    else:
      self.response.out.write(str(blob_info.key()))

    # if self.request.get("download"):
    #   self.redirect('/serve/%s' % str(blob_info.key()))
    # else:
    #   self.response.out.write(str(blob_info.key()))

class ServeHandler(blobstore_handlers.BlobstoreDownloadHandler):
  def get(self, resource):
    resource = str(urllib.unquote(resource))
    blob_info = blobstore.BlobInfo.get(resource)
    self.send_blob(blob_info, save_as=blob_info.filename)



MAIN_ROUTES = [
    ('/', MainHandler),
    ('/journal(\.[a-z]+)?', JournalHandler),
    ('/journal/delete/([^/]+)', JournalDeleteHandler),
    ('/request-upload', RequestUploadHandler),
    ('/upload', UploadHandler),
    ('/serve/([^/]+)?', ServeHandler)
]
