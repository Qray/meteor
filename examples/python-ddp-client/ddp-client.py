#!/usr/bin/env python2.7

from ws4py.client.geventclient import WebSocketClient

import sys
import json
from cmd import Cmd
import readline
import gevent
import string
import argparse
import os

class DDPClient(WebSocketClient):
    def __init__(self, url, onmessage, onclose, print_raw):
        WebSocketClient.__init__(self, url)
        self.connected = False
        self.onmessage = onmessage
        self.print_raw = print_raw
        self.onclose = onclose

    def print_and_send(self, msg_dict):
        message = json.dumps(msg_dict)
        if self.print_raw:
            print '[RAW] >>', message
        self.send(message)

    def opened(self):
        self.connected = True
        self.print_and_send({"msg": "connect"})

    def received_message(self, data):
        if self.print_raw:
            print '[RAW] <<', data
        self.onmessage(str(data))

    def closed(self, code, reason=None):
        self.connected = False
        print '* CONNECTION CLOSED', code, reason
        self.onclose()

class App(Cmd):
    """Main input loop."""

    def __init__(self, print_raw):
        Cmd.__init__(self)

        if sys.stdin.isatty():
            self.prompt = 'DDP> '
        else:
            self.prompt = ''

        self.ddpclient = None
        self.uid = 0

        # Should we print the raw websocket messages in addition to parsing
        # them?
        self.print_raw = print_raw

        # We keep track of methods and subs that have been sent from the client
        # so that we only return to the prompt or quit the app once we get
        # back all the results from the server
        self.pending_method_id = None
        self.pending_method_result_acked = False
        self.pending_method_data_acked = False

        self.pending_sub_id = None
        self.pending_sub_data_acked = False

    ###
    ### The `method` command
    ###
    def help_method(self):
        print '\n'.join(['method <method name> <json array of parameters>',
                         '  Calls a remote method',
                         '  Example: method createApp ' +
                         '[{"name": "foo.meteor.com", ' +
                         '"description": "bar"}]']);

    def do_method(self, params):
        try:
            method_name,params = self.parse_command(params)
        except:
            print 'Error parsing parameter list - try `help method`'
            return

        id = self.next_id()
        self.ddpclient.print_and_send({"msg": "method",
                                       "method": method_name,
                                       "params": params,
                                       "id": id})
        self.block_until_method_fully_returns(id)

    def block_until_method_fully_returns(self, id):
        """Wait until the last call to method gets back all necessary data
        from the server"""
        self.pending_method_id = id
        while True:
            if self.pending_method_result_acked and self.pending_method_data_acked:
                self.pending_method_result_acked = False
                self.pending_method_data_acked = False
                self.pending_method_id = None
                return
            else:
                gevent.sleep(0.1)

    ###
    ### The `sub` command
    ###
    def help_sub(self):
        print '\n'.join(['sub <subscription name> [<json array of parameters>]',
                         '  Subscribes to a remote dataset',
                         '  Examples: `sub allApps` or `sub myApp ["foo.meteor.com"]`'])

    def do_sub(self, params):
        try:
            sub_name,params = self.parse_command(params)
        except:
            print 'Error parsing parameter list - try `help sub`'
            return

        id = self.next_id()
        self.ddpclient.print_and_send({"msg": "sub",
                                       "name": sub_name,
                                       "params": params,
                                       "id": id})
        self.block_until_sub_fully_returns(id)

    def block_until_sub_fully_returns(self, id):
        """Wait until the last call to sub gets back all necessary data
        from the server"""
        self.pending_sub_id = id
        while True:
            if self.pending_sub_data_acked:
                self.pending_sub_data_acked = False
                self.pending_sub_id = None
                return
            else:
                gevent.sleep(0.1)

    ###
    ### The `EOF` "command" (to support `cat file | python ddpclient.py`)
    ###
    def do_EOF(self, line):
        return True

    ###
    ### Auxiliary methods
    ###
    def next_id(self):
        self.uid = self.uid + 1
        return str(self.uid)

    # Parses a command with a first string param and a second json-encoded param
    def parse_command(self, params):
        split_params = string.split(params, ' ')
        name = split_params[0]

        if len(split_params) == 1:
            params = []
        else:
            params = json.loads(' '.join(split_params[1:]))

        return name,params


    def onmessage(self, message):
        """Parse an incoming message, printing and updating the various
        pending_* attributes as appropriate"""

        map = json.loads(message)
        if map.get('msg') == 'error':
            # Reset all pending state
            self.pending_sub_data_acked = True
            self.pending_method_data_acked = True
            self.pending_method_result_acked = True
            print "* ERROR", map['reason']
        elif map.get('msg') == 'connected':
            print "* CONNECTED"
        elif map.get('msg') == 'result':
            if map['id'] == self.pending_method_id:
                self.pending_method_result_acked = True
                if map.get('result'):
                    print "* METHOD RESULT", map['result']
                elif map.get('error'):
                    self.pending_method_data_acked = True
                    self.pending_method_result_acked = True
                    print "* ERROR:", map['error']['reason']
        elif map.get('msg') == 'data':
            if map.get('collection'):
                if map.get('set'):
                    for key, value in map['set'].items():
                        print "* SET", map['collection'], map['id'], key, value
                if map.get('unset'):
                    for key in map['unset']:
                        print "* UNSET", map['collection'], map['id'], key
            if map.get('methods'):
                if self.pending_method_id in map['methods']:
                    self.pending_method_data_acked = True
            if map.get('subs'):
                if self.pending_sub_id in map['subs']:
                    self.pending_sub_data_acked = True
                    print "* SUB COMPLETE"
        elif map.get('msg') == 'nosub':
            self.pending_sub_data_acked = True
            print "* NO SUCH SUB"
    def onclose(self):
        sys.exit(1)

    def connect(self, url):
        self.ddpclient = DDPClient(url, self.onmessage, self.onclose, self.print_raw)
        self.ddpclient.connect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='A command-line tool for communicating with a DDP server.')
    parser.add_argument('ddp_endpoint', metavar='ddp_endpoint',
                        help='DDP websocket endpoint to connect ' +
                        'to, e.g. ws://foo.meteor.com/websocket')
    parser.add_argument('--print-raw', dest='print_raw', action="store_true",
                        help='print raw websocket data in addition to parsed results')
    args = parser.parse_args()

    app = App(print_raw=args.print_raw)
    app.connect(args.ddp_endpoint)
    while not app.ddpclient.connected:
        gevent.sleep(0.1)
    app.cmdloop()






