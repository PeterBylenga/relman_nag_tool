#!/usr/bin/env python
"""
hiA script for automated nagging emails listing all the bugs being tracked by certain queries
These can be collated into several 'queries' through the use of multiple query files with 
a 'query_name' param set eg: 'Bugs tracked for Firefox Beta (13)'
Once the bugs have been collected from bugzilla they are sorted into buckets by assignee manager
and an email can be sent out to the assignees, cc'ing their manager about which bugs are being tracked
for each query
"""
import sys, os
import json
import smtplib
import time
import subprocess
import urllib
import phonebook
import tempfile
import urllib2
import simplejson
import re
from datetime import datetime
from dateutil.parser import parse
from argparse import ArgumentParser
from bugzilla.agents import BMOAgent
from bugzilla.utils import get_credentials
from bugzilla.models import DATETIME_FORMAT, DATETIME_FORMAT_WITH_SECONDS
import flask
from jinja2 import Template, Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))

REPLY_TO_EMAIL = 'release-mgmt@mozilla.com'
EMAIL_SUBJECT = '11/13 - Bugs tracked for Firefox 18/19'
SMTP = 'smtp.mozilla.org'

# TODO - keyword groupings for wiki output (and maybe for emails too?)
# TODO - write tests!
# TODO - look into knocking out duplicated bugs in queries -- perhaps print out if there are dupes in queries when queries > 1
# TODO - for wiki page generation, just post counts of certain query results (and their queries) eg: how many unverified fixed bugs for esr10?

def get_last_manager_comment(comments, manager):
    # go through in reverse order to get most recent
    for comment in comments[::-1]:
        if person != None:
            if comment.creator.name == manager['mozillaMail'] or comment.creator.name == manager['bugzillaEmail']:
                # DEBUG 
                # print "Found last manager (%s) comment on bug. %s" % (comment.creator.real_name, comment.creation_time.replace(tzinfo=None))
                return comment.creation_time.replace(tzinfo=None)
    return None

def get_last_assignee_comment(comments, person):
    # go through in reverse order to get most recent
    for comment in comments[::-1]:
        if person != None:
            if comment.creator.name == person['mozillaMail'] or comment.creator.name == person['bugzillaEmail']:
                # DEBUG
                # print "Found last assignee (%s) comment on bug. %s" % (comment.creator.real_name, comment.creation_time.replace(tzinfo=None))
                return comment.creation_time.replace(tzinfo=None)
    return None
#
# Dictionary cannot hold same keys with multiple value
# so using list -> tuple and encoding with urllib.urlencode
# which can be safely appended to base url path to get openable
# full URL path. See get_bug_list in agents.py
def query_url_to_urlencoded(url):
    print "in query url\n"
    fields_and_values = url.split("?")[1].split("&")
    d = []

    for pair in fields_and_values:
        (key,val) = pair.split("=")
        print key, ":", val ,"\n"
        if key != "list_id":
            #d[key]=urllib.unquote(val)
            d.append((key, urllib.unquote(val)))
    #print "dict ", d
    return urllib.urlencode(tuple(d))

def generateEmailOutput(people, queries, template, show_summary=False, show_comment=False, manager_email=None, 
                    cc_list=None):
    template_params = {}
    toaddrs = []
    #people = flask.session['people']
    #people = phonebook.PhonebookDirectory(flask.session['username'],flask.session['password']);
    # stripping off the templates dir, just in case it gets passed in the args
    #template = env.get_template(template.replace('templates/', '', 1))
    print "Generating email now..."
    t = Template(template)
    message_body = t.render(queries=template_params, show_summary=show_summary, show_comment=show_comment)
    
    for query,results in queries.items():
        template_params[query] = {'buglist': []}
        for bug in results['bugs']:
            template_params[query]['buglist'].append({
                    'id':bug.id,
                    'summary':bug.summary,
                    #'comment': bug.comments[-1].creation_time.replace(tzinfo=None),
                    'assignee': bug.assigned_to.real_name
            })
            # more hacking for JS special casing
            if bug.assigned_to.name == 'general@js.bugs' and 'dmandelin@mozilla.com' not in toaddrs:
                toaddrs.append('dmandelin@mozilla.com')
            
            if people.people_by_bzmail.has_key(bug.assigned_to.name):
                person = dict(people.people_by_bzmail[bug.assigned_to.name])
                if person['mozillaMail'] not in toaddrs:
                    toaddrs.append(person['mozillaMail'])
            elif people.people_by_bzmail.has_key(manager_email):
                person = dict(people.people_by_bzmail[manager_email])
                if person['mozillaMail'] not in toaddrs:
                    toaddrs.append(person['mozillaMail'])
            else:
                if manager_email not in toaddrs:
                    toaddrs.append(manager_email)
    print 'To: ', toaddrs
    message_body = t.render(queries=template_params, show_summary=show_summary, show_comment=show_comment)
    # is our only email to a manager? then only cc the REPLY_TO_EMAIL
    manager = dict(people.people[manager_email])
    if len(toaddrs) == 1 and toaddrs[0] == manager_email or toaddrs[0] == manager.get('bugzillaMail'):
        if toaddrs[0] == 'dmandelin@mozilla.com':
            cc_list = [REPLY_TO_EMAIL, 'danderson@mozilla.com','nihsanullah@mozilla.com']
        else:
            cc_list = [REPLY_TO_EMAIL]
        print "Debug, not cc'ing a manager"
    else:
        if cc_list == None:
            if manager_email == 'dmandelin@mozilla.com':
                cc_list = [manager_email, REPLY_TO_EMAIL, 'danderson@mozilla.com', 'nihsanullah@mozilla.com']
            else:
                cc_list = [manager_email, REPLY_TO_EMAIL]
        # no need to send to as well as cc a manager
        for email in toaddrs:
            if email in cc_list:
                toaddrs.remove(email)
    print 'Cc: ', cc_list
    message_subject = EMAIL_SUBJECT
    message = ("From: %s\r\n" % REPLY_TO_EMAIL
        + "To: %s\r\n" % ",".join(toaddrs)
        + "CC: %s\r\n" % ",".join(cc_list)
        + "Reply-To: %s\r\n" % REPLY_TO_EMAIL
        + "Subject: %s\r\n" % message_subject
        + "\r\n" 
        + message_body.strip())
    toaddrs = toaddrs + cc_list
    return message


def sendMail(msg):
    
    toaddrs=msg.split("To: ")[1].split("\r\n")[0].split(',') + msg.split("CC: ")[1].split("\r\n")[0].split(',')
    server = smtplib.SMTP_SSL(SMTP, 465)
    server.set_debuglevel(1)
    username = flask.session["username"]
    password = flask.session["password"]
    server.login(username, password)
    # note: toaddrs is required for transport agents, the msg['To'] header is not modified
    server.sendmail(username,toaddrs, msg)
    server.quit()

def nagEmailScript():
    print "*******In nagEmailScript"
    email_cc_list=['release-mgmt@mozilla.com']
    
    # Load our agent for BMO
    username = flask.session['username']
    password = flask.session['password']
    
    bmo = BMOAgent(username, password)
    bmo.check_login("https://bugzilla.mozilla.org/show_bug.cgi?id=12");
    people = phonebook.PhonebookDirectory(flask.session['username'],flask.session['password']);
    queries = flask.session['queries'] 
    
    # Get the buglist(s)
    collected_queries = {}
    
    for query in queries:
    # import the query
        query_name = query['query_name']
        print "\nquery_name\n",query_name
        print "\nquery_channel\n",query['query_channel']
        collected_queries[query_name] = {
            'channel': query['query_channel'],
            'bugs' : [],
            }
        print "\n\nafter import query"
        if query.has_key('query_params'):
            print "Gathering bugs from query_params in %s" % query
            collected_queries[query_name]['bugs'] = bmo.get_bug_list(query['query_params'])
        elif query.has_key('query_url'):
            print "Gathering bugs from query_url in %s" % query
            collected_queries[query_name]['bugs'] = bmo.get_bug_list(query_url_to_urlencoded(query['query_url']))
        else:
            raise Exception("Error - no valid query params or url in the config file")
            
    total_bugs = 0
    for channel in collected_queries.keys():
        total_bugs += len(collected_queries[channel]['bugs'])

    print "Found %s bugs total for %s queries" % (total_bugs, len(collected_queries.keys()))
    print "Queries to collect: %s" % collected_queries.keys()
    managers = people.managers
    manual_notify = []
    counter = 0
    
    def add_to_managers(manager_email, query):
        if managers.has_key(manager_email):
            if managers[manager_email].has_key('nagging'):
                if managers[manager_email]['nagging'].has_key(query):
                    managers[manager_email]['nagging'][query]['bugs'].append(bug)
                    print "Adding %s to %s in nagging for %s" % (bug.id, query, manager_email)
                else:
                    managers[manager_email]['nagging'][query] = { 'bugs': [bug] }
                    print "Adding new query key %s for bug %s in nagging and %s" % (query, bug.id, manager_email)
            else:
                managers[manager_email]['nagging'] = {
                        query : { 'bugs': [bug] },
                    }
                print "Creating query key %s for bug %s in nagging and %s" % (query, bug.id, manager_email)
        else :
            print "Email not found in phonebook: ", manager_email
            managers[manager_email] = {}
            add_to_managers(manager_email, query)
  
    verbose = False
    for query in collected_queries.keys():
        for b in collected_queries[query]['bugs']:
            if verbose:
                print "\nb::::",b
            counter = counter + 1
            send_mail = True
            bug = bmo.get_bug(b.id)
            if verbose:
                print "\nYYYYYYYYYYYYYYYYYYYYY210\n"
            manual_notify.append(bug)
            assignee = bug.assigned_to.name
            if people.people_by_bzmail.has_key(assignee):
                person = dict(people.people_by_bzmail[assignee])
            else:
                person = None
            if verbose:
                print "\nYYYYYYYYYYYYYYYYYYYYY217\n"
            if send_mail:
                if verbose:
                    print "\nYYYYYYYYYYYYYYYYYYYYY219\n"
                if 'nobody' in assignee:
                    #getting the list of owners externally from Andreas's dashboard 
                    owners_js = urllib2.urlopen("http://andreasgal.github.io/dashboard/owners.js").read(10000)
                    
                    #formatting that js file that declares a variable into a json-ish string
                    owners_js = owners_js.split('{')
                    owners_js = "{" + owners_js[1]
                    owners_js = owners_js.split(';')
                    owners_js = owners_js[0]

                    #get rid of comments in the json file, run until all comments are gone
                    while owners_js.find('//') > 0 or owners_js.find('/*') > 0 :
                        owners_js = re.sub('//.*?\n|/\*.*?\*/', '\n', owners_js, re.S)
                    
                    #removing bracketed 2nds
                    while owners_js.find('(') > 0 or owners_js.find(')') > 0 :
                        owners_js = re.sub('\s*?\(.*?\)\s*?', '', owners_js, re.S)
                    
                    #removing trailing comma
                    owners_js = re.sub('{', '', owners_js)
                    owners_js = re.sub('}', '', owners_js)
                    owners_js = owners_js.strip(' \t\n\r')
                    owners_js = owners_js.strip(',')
                    owners_js = '{\n' + owners_js + '\n}'
                    
                    #search for email of the responsible component owner
                    owners_js = simplejson.loads(owners_js)
                    if ( owners_js.has_key(bug.component) ):
                        component_manager_name = owners_js[bug.component]
                        if people.people_by_name.has_key(component_manager_name) :
                            component_manager = people.people_by_name[component_manager_name]
                            if component_manager['mozillaMail'] != None:
                                add_to_managers(component_manager['mozillaMail'], query)
                            else:
                                assignee = None
                        else :
                            print "Name not found in phonebook: ", component_manager_name 
                            assignee = None
                    else:
                        assignee = None

                # TODO - get rid of this, SUCH A HACK!
                elif 'general@js.bugs' in assignee:
                    #dmandelin is no longer with the firm so this is changed yo naveed
                    #Note to self do perform error handling in add_to_managers
                    print "No one assigned to JS bug: %s, adding to dmandelin's list..." % bug.id
                    add_to_managers('nihsanullah@mozilla.com', query)
                else:
                    if bug.assigned_to.real_name != None:
                        if person != None:
                            # check if assignee is already a manager, add to their own list
                            if managers.has_key(person['mozillaMail']):
                                add_to_managers(person['mozillaMail'], query)
                            # otherwise we search for the assignee's manager
                            else:
                                # check for manager key first, a few people don't have them
                                if person.has_key('manager') and person['manager'] != None:
                                    manager_email = person['manager']['dn'].split('mail=')[1].split(',')[0]
                                    if managers.has_key(manager_email):
                                        add_to_managers(manager_email, query)
                                    elif people.vices.has_key(manager_email):
                                        # we're already at the highest level we'll go
                                        if managers.has_key(assignee):
                                            add_to_managers(assignee, query)
                                        else:
                                            managers[person['mozillaMail']] = {}
                                            add_to_managers(person['mozillaMail'], query)
                                    else:
                                        # try to go up one level and see if we find a manager
                                        if people.people.has_key(manager_email):
                                            person = dict(people.people[manager_email])
                                            manager_email = person['manager']['dn'].split('mail=')[1].split(',')[0]
                                            if managers.has_key(manager_email):
                                                add_to_managers(manager_email, query)
                                        else:
                                            print "Manager could not be found: %s" % manager_email
                                else:
                                    print "%s's entry doesn't list a manager! Let's ask them to update phonebook." % person['name']
    send_msg = []
    manual_notify_msg =''
    for email, info in managers.items():
        if info.has_key('nagging'):
            print "\n\nIn nagging"
            msg = generateEmailOutput(
            people,
            manager_email=email,
            queries=info['nagging'],
            template=flask.session['modified_template'],
            show_summary=True,
            show_comment=False)
            send_msg.append(msg)
            sent_bugs = 0
            for query, info in info['nagging'].items():
                sent_bugs += len(info['bugs'])
                # take sent bugs out of manual notification list
                for bug in info['bugs']:
                    manual_notify.remove(bug)
                counter = counter - sent_bugs
    
    
  # output the manual notification list
    manual_notify_msg += "No email generated for %s/%s bugs, you will need to manually notify the following %s bugs:\n\n" % (counter, total_bugs, len(manual_notify))
    url = "https://bugzilla.mozilla.org/buglist.cgi?quicksearch="
    
    for bug in manual_notify:
        manual_notify_msg += "[Bug %s] -- assigned to: %s -- Last commented on: %s\n" % (bug.id, bug.assigned_to.real_name, bug.comments[-1].creation_time.replace(tzinfo=None))
        url += "%s," % bug.id
    manual_notify_msg += "\n\nUrl for manual notification bug list: %s \n" % url
    #print send_msg
    return send_msg, manual_notify_msg


    
    
    if not managers:
        msg = "\n*************\nNo email generated for %s/%s bugs, you will need to manually notify the following %s bugs:\n" % (counter, flask.session['total_bugs'] , len(manual_notify))
        url = "https://bugzilla.mozilla.org/buglist.cgi?quicksearch="
    
        for bug in manual_notify:
            msg += "[Bug %s] -- assigned to: %s\n -- Last commented on: %s\n" % (bug.id, bug.assigned_to.real_name, bug.comments[-1].creation_time.replace(tzinfo=None))
            url += "%s," % bug.id
        msg += "Url for manual notification bug list: %s" % url
        return True, msg
    
    
