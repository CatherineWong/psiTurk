# coding: utf-8
import sys
import subprocess
import re
import time
import json
import os
import string
import random
import datetime

from cmd2 import Cmd
from docopt import docopt, DocoptExit
import readline

import webbrowser

import sqlalchemy as sa

from amt_services import MTurkServices, RDSServices
from psiturk_org_services import PsiturkOrgServices
from version import version_number
from psiturk_config import PsiturkConfig
import experiment_server_controller as control
from db import db_session, init_db
from models import Participant

# Escape sequences for display.
def colorize(target, color):
    colored = ''
    if color == 'purple':
        colored = '\001\033[95m\002' + target
    elif color == 'cyan':
        colored = '\001\033[96m\002' + target
    elif color == 'darkcyan':
        colored = '\001\033[36m\002' + target
    elif color == 'blue':
        colored = '\001\033[93m\002' + target
    elif color == 'green':
        colored = '\001\033[92m\002' + target
    elif color == 'yellow':
        colored = '\001\033[93m\002' + target
    elif color == 'red':
        colored = '\001\033[91m\002' + target
    elif color == 'white':
        colored = '\001\033[37m\002' + target
    elif color == 'bold':
        colored = '\001\033[1m\002' + target
    elif color == 'underline':
        colored = '\001\033[4m\002' + target
    return colored + '\001\033[0m\002'


# Decorator function borrowed from docopt.
def docopt_cmd(func):
    """
    This decorator is used to simplify the try/except block and pass the result
    of the docopt parsing to the called action.
    """
    def fn(self, arg):
        try:
            opt = docopt(fn.__doc__, arg)
        except DocoptExit as e:
            # The DocoptExit is thrown when the args do not match.
            # We print a message to the user and the usage block.
            print('Invalid Command!')
            print(e)
            return
        except SystemExit:
            # The SystemExit exception prints the usage for --help
            # We do not need to do the print here.
            return
        return func(self, opt)
    fn.__name__ = func.__name__
    fn.__doc__ = func.__doc__
    fn.__dict__.update(func.__dict__)
    return fn


#---------------------------------
# psiturk shell class
#  -  all commands contained in methods titled do_XXXXX(self, arg)
#  -  if a command takes any arguments, use @docopt_cmd decorator
#     and describe command usage in docstring
#---------------------------------
class PsiturkShell(Cmd, object):
    """
    Usage:
        psiturk -c
        psiturk_shell -c
    """

    def __init__(self, config, server):
        Cmd.__init__(self)
        self.config = config
        self.server = server

        # Prevents running of commands by abbreviation
        self.abbrev = False
        self.debug = True
        self.helpPath = os.path.join(os.path.dirname(__file__), "shell_help/")
        self.psiTurk_header = 'psiTurk command help:'
        self.super_header = 'basic CMD command help:'

        self.color_prompt()
        self.intro = self.get_intro_prompt()


    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  basic command line functions
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def check_offline_configuration(self):
        quit_on_start = False
        database_url = self.config.get('Database Parameters', 'database_url')
        host = self.config.get('Server Parameters', 'host', 'localhost')
        if database_url[:6] != 'sqlite':
            print "*** Error: config.txt option 'database_url' set to use mysql://.  Please change this sqllite:// while in cabin mode."
            quit_on_start = True
        if host != 'localhost':
            print "*** Error: config option 'host' is not set to localhost.  Please change this to localhost while in cabin mode."
            quit_on_start = True
        if quit_on_start:
            exit()

    def get_intro_prompt(self):
        # offline message
        sysStatus = open(self.helpPath + 'cabin.txt', 'r')
        server_msg = sysStatus.read()
        return server_msg + colorize('psiTurk version ' + version_number +
                              '\nType "help" for more information.', 'green')

    def do_system_status(self, args):
        print self.get_intro_prompt()

    def color_prompt(self):
        prompt = '[' + colorize('psiTurk', 'bold')
        serverString = ''
        server_status = self.server.is_server_running()
        if server_status == 'yes':
            serverString = colorize('on', 'green')
        elif server_status == 'no':
            serverString = colorize('off', 'red')
        elif server_status == 'maybe':
            serverString = colorize('wait', 'yellow')
        prompt += ' server:' + serverString
        prompt += ' mode:' + colorize('cabin', 'bold')
        prompt += ']$ '
        self.prompt = prompt

    # keep persistent command history
    def preloop(self):
        # create file if it doesn't exist
        open('.psiturk_history', 'a').close()
        readline.read_history_file('.psiturk_history')
        for i in range(readline.get_current_history_length()):
            if readline.get_history_item(i) != None:
                self.history.append(readline.get_history_item(i))
        Cmd.preloop(self)

    def postloop(self):
        readline.write_history_file('.psiturk_history')
        Cmd.postloop(self)

    def onecmd_plus_hooks(self, line):
        if not line:
            return self.emptyline()
        return Cmd.onecmd_plus_hooks(self, line)

    def postcmd(self, stop, line):
        self.color_prompt()
        return Cmd.postcmd(self, stop, line)

    def emptyline(self):
        self.color_prompt()

    # add space after a completion, makes tab completion with
    # multi-word commands cleaner
    def complete(self, text, state):
        return Cmd.complete(self, text, state) + ' '


    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  server management
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def server_launch(self):
        self.server.startup()
        while self.server.is_server_running() != 'yes':
            time.sleep(0.5)

    def server_shutdown(self):
        self.server.shutdown()
        print 'Please wait. This could take a few seconds.'
        while self.server.is_server_running() != 'no':
            time.sleep(0.5)

    def server_relaunch(self):
        self.server_shutdown()
        self.server_launch()

    def server_log(self):
        logfilename = self.config.get('Server Parameters', 'logfile')
        if sys.platform == "darwin":
            args = ["open", "-a", "Console.app", logfilename]
        else:
            args = ["xterm", "-e", "'tail -f %s'" % logfilename]
        subprocess.Popen(args, close_fds=True)
        print "Log program launching..."

    @docopt_cmd
    def do_debug(self, arg):
        """
        Usage: debug [options]

        -p, --print-only         just provides the URL, doesn't attempt to launch browser
        """
        if self.server.is_server_running() == 'no' or self.server.is_server_running()=='maybe':
            print "Error: Sorry, you need to have the server running to debug your experiment.  Try 'server launch' first."
            return

        base_url = "http://" + self.config.get('Server Parameters', 'host') + ":" + self.config.get('Server Parameters', 'port') + "/ad"
        launchurl = base_url + "?assignmentId=debug" + str(self.random_id_generator()) \
                    + "&hitId=debug" + str(self.random_id_generator()) \
                    + "&workerId=debug" + str(self.random_id_generator())

        if arg['--print-only']:
            print "Here's your randomized debug link, feel free to request another:\n\t", launchurl
        else:
            print "Launching browser pointed at your randomized debug link, feel free to request another.\n\t", launchurl
            webbrowser.open(launchurl, new=1, autoraise=True)

    def help_debug(self):
        with open(self.helpPath + 'debug.txt', 'r') as helpText:
            print helpText.read()

    def do_version(self, arg):
        print 'psiTurk version ' + version_number

    def do_print_config(self, arg):
        for section in self.config.sections():
            print '[%s]' % section
            items = dict(self.config.items(section))
            for k in items:
                print "%(a)s=%(b)s" % {'a': k, 'b': items[k]}
            print ''
            
    def do_reload_config(self, arg):
        self.config.load_config()

    def do_status(self, arg):
        server_status = self.server.is_server_running()
        if server_status == 'yes':
            print 'Server: ' + colorize('currently online', 'green')
        elif server_status == 'no':
            print 'Server: ' + colorize('currently offline', 'red')
        elif server_status == 'maybe':
            print 'Server: ' + colorize('please wait', 'yellow')

    def do_setup_example(self, arg):
        import setup_example as se
        se.setup_example()


    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  Local SQL database commands
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def db_get_config(self):
        print "Current database setting (database_url): \n\t", self.config.get("Database Parameters", "database_url")
    
    def db_use_local_file(self, filename=None):
        interactive = False
        if filename is None:
            interactive = True
            filename = raw_input('Enter the filename of the local SQLLite database you would like to use [default=participants.db]: ')
            if filename=='':
                filename='participants.db'
        base_url = "sqlite:///" + filename
        self.config.set("Database Parameters", "database_url", base_url)
        print "Updated database setting (database_url): \n\t", self.config.get("Database Parameters", "database_url")
        if self.server.is_server_running() == 'yes':
            self.server_relaunch()

    def do_download_datafiles(self, arg):
        contents = {"trialdata": lambda p: p.get_trial_data(), "eventdata": lambda p: p.get_event_data(), "questiondata": lambda p: p.get_question_data()}
        query = Participant.query.all()
        for k in contents:
            ret = "".join([contents[k](p) for p in query])
            f = open(k + '.csv', 'w')
            f.write(ret)
            f.close()

    @docopt_cmd
    def do_open(self, arg):
        """
        Usage: open
               open <folder>

        Opens folder or current directory using the local system's shell comamnd 'open'.
        """
        if arg['<folder>'] is None:
            subprocess.call(["open"])
        else:
            subprocess.call(["open",arg['<folder>']])

    def do_eof(self, arg):
        self.do_quit(arg)
        return True

    def do_exit(self, arg):
        self.do_quit(arg)
        return True

    def do_quit(self, arg):
        if self.server.is_server_running() == 'yes' or self.server.is_server_running() == 'maybe':
            r = raw_input("Quitting shell will shut down experiment server. Really quit? y or n: ")
            if r == 'y':
                self.server_shutdown()
            else:
                return
        return True

    @docopt_cmd
    def do_server(self, arg):
        """
        Usage: 
          server launch
          server shutdown
          server relaunch
          server log
          server help
        """
        if arg['launch']:
            self.server_launch()
        elif arg['shutdown']:
            self.server_shutdown()
        elif arg['relaunch']:
            self.server_relaunch()
        elif arg['log']:
            self.server_log()
        else:
            self.help_server()

    server_commands = ('launch', 'shutdown', 'relaunch', 'log', 'help')

    def complete_server(self, text, line, begidx, endidx):
        return  [i for i in PsiturkShell.server_commands if i.startswith(text)]

    def help_server(self):
        with open(self.helpPath + 'server.txt', 'r') as helpText:
            print helpText.read()

    def random_id_generator(self, size = 6, chars = string.ascii_uppercase + string.digits):
        return ''.join(random.choice(chars) for x in range(size))

    # modified version of standard cmd help which lists psiturk commands first
    def do_help(self, arg):
        if arg:
            try:
                func = getattr(self, 'help_' + arg)
            except AttributeError:
                try:
                    doc = getattr(self, 'do_' + arg).__doc__
                    if doc:
                        self.stdout.write("%s\n" % str(doc))
                        return
                except AttributeError:
                    pass
                self.stdout.write("%s\n" % str(self.nohelp % (arg,)))
                return
            func()
        else:
            # Modifications start here
            names = dir(PsiturkShell)
            superNames = dir(Cmd)
            newNames = [m for m in names if m not in superNames]
            help = {}
            cmds_psiTurk = []
            cmds_super = []
            for name in names:
                if name[:5] == 'help_':
                    help[name[5:]]=1
            names.sort()
            prevname = ''
            for name in names:
                if name[:3] == 'do_':
                    if name == prevname:
                        continue
                    prevname = name
                    cmd = name[3:]
                    if cmd in help:
                        del help[cmd]
                    if name in newNames:
                        cmds_psiTurk.append(cmd)
                    else:
                        cmds_super.append(cmd)
            self.stdout.write("%s\n" % str(self.doc_leader))
            self.print_topics(self.psiTurk_header, cmds_psiTurk, 15, 80)
            self.print_topics(self.misc_header, help.keys(), 15, 80)
            self.print_topics(self.super_header, cmds_super, 15, 80)


class PsiturkNetworkShell(PsiturkShell):

    def __init__(self, config, amt_services, aws_rds_services, web_services, server):
        self.config = config
        self.amt_services = amt_services
        self.web_services = web_services
        self.db_services = aws_rds_services
        self.sandbox = self.config.getboolean('HIT Configuration', 
                                              'using_sandbox')


        self.sandboxHITs = 0
        self.liveHITs = 0
        self.tally_hits()
        PsiturkShell.__init__(self, config, server)

        # Prevents running of commands by abbreviation
        self.abbrev = False
        self.debug = True
        self.helpPath = os.path.join(os.path.dirname(__file__), "shell_help/")
        self.psiTurk_header = 'psiTurk command help:'
        self.super_header = 'basic CMD command help:'



    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  basic command line functions
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def get_intro_prompt(self):  # overloads intro prompt with network-aware version
        # if you can reach psiTurk.org, request system status
        # message
        server_msg = self.web_services.get_system_status()
        return server_msg + colorize('psiTurk version ' + version_number +
                              '\nType "help" for more information.', 'green')

    def color_prompt(self):  # overloads prompt with network info
        prompt = '[' + colorize('psiTurk', 'bold')
        serverString = ''
        server_status = self.server.is_server_running()
        if server_status == 'yes':
            serverString = colorize('on', 'green')
        elif server_status == 'no':
            serverString = colorize('off', 'red')
        elif server_status == 'maybe':
            serverString = colorize('wait', 'yellow')
        prompt += ' server:' + serverString
        if self.sandbox:
            prompt += ' mode:' + colorize('sdbx', 'bold')
        else:
            prompt += ' mode:' + colorize('live', 'bold')
        if self.sandbox:
            prompt += ' #HITs:' + str(self.sandboxHITs)
        else:
            prompt += ' #HITs:' + str(self.liveHITs)
        prompt += ']$ '
        self.prompt = prompt

    def do_status(self, arg): # overloads do_status with AMT info
        super(PsiturkNetworkShell, self).do_status(arg)
        server_status = self.server.is_server_running()
        self.tally_hits()
        if self.sandbox:
            print 'AMT worker site - ' + colorize('sandbox', 'bold') + ': ' + str(self.sandboxHITs) + ' HITs available'
        else:
            print 'AMT worker site - ' + colorize('live', 'bold') + ': ' + str(self.liveHITs) + ' HITs available'


    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  worker management
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def worker_list(self, submitted, approved, rejected, allWorkers, chosenHit):
        workers = None
        if submitted:
            workers = self.amt_services.get_workers("Submitted")
        elif approved:
            workers = self.amt_services.get_workers("Approved")
        elif rejected:
            workers = self.amt_services.get_workers("Rejected")
        else:
            workers = self.amt_services.get_workers()
        if workers==False:
            print colorize('*** failed to get workers', 'red')
        if chosenHit:
            workers = [worker for worker in workers if worker['hitId']==chosenHit]
            print 'listing workers for HIT', chosenHit
        if not len(workers):
            print "*** no workers match your request"
        else:
            print json.dumps(workers, indent=4,
                             separators=(',', ': '))

    def worker_approve(self, chosenHit, assignment_ids = None):
        if chosenHit:
            workers = self.amt_services.get_workers("Submitted")
            assignment_ids = [worker['assignmentId'] for worker in workers if worker['hitId']==chosenHit]
            print 'approving workers for HIT', chosenHit
        for assignmentID in assignment_ids:
            success = self.amt_services.approve_worker(assignmentID)
            if success:
                print 'approved', assignmentID
            else:
                print '*** failed to approve', assignmentID

    def worker_reject(self, chosenHit, assignment_ids = None):
        if chosenHit:
            workers = self.amt_services.get_workers("Submitted")
            assignment_ids = [worker['assignmentId'] for worker in workers if worker['hitId']==chosenHit]
            print 'rejecting workers for HIT',chosenHit
        for assignmentID in assignment_ids:
            success = self.amt_services.reject_worker(assignmentID)
            if success:
                print 'rejected', assignmentID
            else:
                print '*** failed to reject', assignmentID
    
    def worker_bonus(self, chosenHit, auto, amount, reason, assignment_ids = None):
        while not reason:
            r = raw_input("Type the reason for the bonus. Workers will see this message: ")
            reason = r
        #bonus already-bonused workers if the user explicitly lists their worker IDs
        overrideStatus = True
        if chosenHit:        
            overrideStatus = False
            workers = self.amt_services.get_workers("Approved")
            if workers==False:
                print "No approved workers for HIT", chosenHit
                return
            assignment_ids = [worker['assignmentId'] for worker in workers if worker['hitId']==chosenHit]
            print 'bonusing workers for HIT', chosenHit
        for assignmentID in assignment_ids:
            try:
                init_db()
                part = Participant.query.\
                       filter(Participant.assignmentid == assignmentID).\
                       one()
                if auto:
                    amount = part.bonus
                status = part.status
                if amount<=0:
                    print "bonus amount <=$0, no bonus given to", assignmentID
                elif status==6 and not overrideStatus:
                    print "bonus already awarded to ", assignmentID
                else:
                    success = self.amt_services.bonus_worker(assignmentID, amount, reason)
                    if success:
                        print "gave bonus of $" + str(amount) + " to " + assignmentID
                        part.status = 6
                    else:
                        print "*** failed to bonus", assignmentID
            except:
                print "*** failed to bonus", assignmentID

    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  hit management
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def amt_balance(self):
        print self.amt_services.check_balance()


    def hit_list(self, allHits, activeHits, reviewableHits):
        hits_data = []
        if allHits:
            hits_data = self.amt_services.get_all_hits()
        elif activeHits:
            hits_data = self.amt_services.get_active_hits()
        elif reviewableHits:
            hits_data = self.amt_services.get_reviewable_hits()
        if not hits_data:
            print '*** no hits retrieved'
        else:
            for hit in hits_data:
                print hit

    def hit_extend(self, hitID, assignments, minutes):
        """ Add additional worker assignments or minutes to a HIT.

        Args:
            hitID: A list conaining one hitID string.
            assignments: Variable <int> for number of assignments to add.
            minutes: Variable <int> for number of minutes to add.

        Returns:
            A side effect of this function is that the state of a HIT changes on AMT servers.

        Raises:

        """

        assert type(hitID) is list
        assert type(hitID[0]) is str

        if self.amt_services.extend_hit(hitID[0], assignments, minutes):
            print "HIT extended."

    def hit_dispose(self, allHits, hitIDs=None):
        if allHits:
            hits_data = self.amt_services.get_all_hits()
            hitIDs = [hit.options['hitid'] for hit in hits_data if (hit.options['status']=="Reviewable")]
        for hit in hitIDs:
            # check that the his is reviewable
            status = self.amt_services.get_hit_status(hit)
            if not status:
                print "*** Error getting hit status"
                return
            if self.amt_services.get_hit_status(hit)!="Reviewable":
                print "*** This hit is not 'Reviewable' and so can not be disposed of"
                return
            else:
                self.amt_services.dispose_hit(hit)
                self.web_services.delete_ad(hit)  # also delete the ad
                if self.sandbox:
                    print "deleting sandbox HIT", hit
                else:
                    print "deleting live HIT", hit

    def hit_expire(self, allHits, hitIDs=None):
        if allHits:
            hits_data = self.amt_services.get_active_hits()
            hitIDs = [hit.options['hitid'] for hit in hits_data]
        for hit in hitIDs:
            self.amt_services.expire_hit(hit)
            if self.sandbox:
                print "expiring sandbox HIT", hit
                self.sandboxHITs -= 1
            else:
                print "expiring live HIT", hit
                self.liveHITs -= 1

    def tally_hits(self):
        hits = self.amt_services.get_active_hits()
        if hits:
            if self.sandbox:
                self.sandboxHITs = len(hits)
            else:
                self.liveHITs = len(hits)


    def hit_create(self, numWorkers, reward, duration):
        interactive = False
        if numWorkers is None:
            interactive = True
            numWorkers = raw_input('number of participants? ')
        try:
            int(numWorkers)
        except ValueError:

            print '*** number of participants must be a whole number'
            return
        if int(numWorkers) <= 0:
            print '*** number of participants must be greater than 0'
            return
        if interactive:
            reward = raw_input('reward per HIT? ')
        p = re.compile('\d*.\d\d')
        m = p.match(reward)
        if m is None:
            print '*** reward must have format [dollars].[cents]'
            return
        if interactive:
            duration = raw_input('duration of hit (in hours)? ')
        try:
            int(duration)
        except ValueError:
            print '*** duration must be a whole number'
            return
        if int(duration) <= 0:
            print '*** duration must be greater than 0'
            return
        self.config.set('HIT Configuration', 'max_assignments',
                        numWorkers)
        self.config.set('HIT Configuration', 'reward', reward)
        self.config.set('HIT Configuration', 'duration', duration)

        # register with the ad server (psiturk.org/ad/register) using POST
        if os.path.exists('templates/ad.html'):
            ad_html = open('templates/ad.html').read()
        else:
            print '*****************************'
            print '  Sorry there was an error registering ad.'
            print "  Both ad.html is required to be in the templates/ folder of your project so that these Ad can be served!"
            return

        size_of_ad = sys.getsizeof(ad_html)
        if size_of_ad >= 1048576:
            print '*****************************'
            print '  Sorry there was an error registering ad.'
            print "  Your local ad.html is %s byes, but the maximum template size uploadable to the Ad server is 1048576 bytes!", size_of_ad
            return

        # what all do we need to send to server?
        # 1. server
        # 2. port
        # 3. support_ie?
        # 4. ad.html template
        # 5. contact_email in case an error happens

        ad_content = {
            "server": str(self.web_services.get_my_ip()),
            "port": str(self.config.get('Server Parameters', 'port')),
            "support_ie": str(self.config.get('Task Parameters', 'support_ie')),
            "is_sandbox": str(self.sandbox),
            "ad.html": ad_html,
            "contact_email": str(self.config.get('Secure Ad Server', 'contact_email'))
        }

        create_failed = False
        ad_id = self.web_services.create_ad(ad_content)
        if ad_id != False:
            ad_url = self.web_services.get_ad_url(ad_id)
            hit_config = {
                "ad_location": ad_url,
                "approve_requirement": self.config.get('HIT Configuration', 'Approve_Requirement'),
                "us_only": self.config.getboolean('HIT Configuration', 'US_only'),
                "lifetime": datetime.timedelta(hours=self.config.getfloat('HIT Configuration', 'lifetime')),
                "max_assignments": self.config.getint('HIT Configuration', 'max_assignments'),
                "title": self.config.get('HIT Configuration', 'title'),
                "description": self.config.get('HIT Configuration', 'description'),
                "keywords": self.config.get('HIT Configuration', 'keywords'),
                "reward": self.config.getfloat('HIT Configuration', 'reward'),
                "duration": datetime.timedelta(hours=self.config.getfloat('HIT Configuration', 'duration'))
            }
            hit_id = self.amt_services.create_hit(hit_config)
            if hit_id != False:
                if not self.web_services.set_ad_hitid(ad_id, hit_id):
                    create_failed = True
            else:
                create_failed = True
        else:
            create_failed = True

        if create_failed:
            print '*****************************'
            print '  Sorry there was an error creating hit and registering ad.'

        else:
            if self.sandbox:
                self.sandboxHITs += 1
            else:
                self.liveHITs += 1
            #print results
            total = float(numWorkers) * float(reward)
            fee = total / 10
            total = total + fee
            location = ''
            if self.sandbox:
                location = 'sandbox'
            else:
                location = 'live'
            print '*****************************'
            print '  Creating %s HIT' % colorize(location, 'bold')
            print '    HITid: ', str(hit_id)
            print '    Max workers: ' + numWorkers
            print '    Reward: $' + reward
            print '    Duration: ' + duration + ' hours'
            print '    Fee: $%.2f' % fee
            print '    ________________________'
            print '    Total: $%.2f' % total
            print '  Ad for this HIT now hosted at: http://psiturk.org/ad/' + str(ad_id) + "?assignmentId=debug" + str(self.random_id_generator()) \
                        + "&hitId=debug" + str(self.random_id_generator())



    @docopt_cmd
    def do_db(self, arg):
        """
        Usage:
          db get_config
          db use_local_file [<filename>]
          db use_aws_instance [<instance_id>]
          db aws_list_regions
          db aws_get_region
          db aws_set_region [<region_name>]
          db aws_list_instances
          db aws_create_instance [<instance_id> <size> <username> <password> <dbname>]
          db aws_delete_instance [<instance_id>]
          db help
        """
        if arg['get_config']:
            self.db_get_config()
        elif arg['use_local_file']:
            self.db_use_local_file(arg['<filename>'])
        elif arg['use_aws_instance']:
            self.db_use_aws_instance(arg['<instance_id>'])
            pass
        elif arg['aws_list_regions']:
            self.db_aws_list_regions()
        elif arg['aws_get_region']:
            self.db_aws_get_region()
        elif arg['aws_set_region']:
            self.db_aws_set_region(arg['<region_name>'])
        elif arg['aws_list_instances']:
            self.db_aws_list_instances()
        elif arg['aws_create_instance']:
            self.db_create_aws_db_instance(arg['<instance_id>'], arg['<size>'], arg['<username>'], arg['<password>'], arg['<dbname>'])
        elif arg['aws_delete_instance']:
            self.db_aws_delete_instance(arg['<instance_id>'])
        else:
            self.help_db()

    db_commands = ('get_config', 'use_local_file', 'use_aws_instance', 'aws_list_regions', 'aws_get_region', 'aws_set_region', 'aws_list_instances', 'aws_create_instance', 'aws_delete_instance', 'help')

    def complete_db(self, text, line, begidx, endidx):
        return  [i for i in PsiturkShell.db_commands if i.startswith(text)]

    def help_db(self):
        with open(self.helpPath + 'db.txt', 'r') as helpText:
            print helpText.read()


    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  AWS RDS commands
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    def db_aws_list_regions(self):
        regions = self.db_services.list_regions()
        if regions != []:
            print "Avaliable AWS regions:"
        for reg in regions:
            print '\t' + reg,
            if reg == self.db_services.get_region():
                print "(currently selected)"
            else:
                print ''

    def db_aws_get_region(self):
        print self.db_services.get_region()

    def db_aws_set_region(self, region_name):
        interactive = False
        if region_name is None:
            interactive = True
            self.db_aws_list_regions()
            allowed_regions = self.db_services.list_regions()
            region_name = "NONSENSE WORD1234"
            tries = 0
            while region_name not in allowed_regions:
                if tries == 0:
                    region_name = raw_input('Enter the name of the region you would like to use: ')
                else:
                    print "*** The region name (%s) you entered is not allowed, please choose from the list printed above (use type 'db aws_list_regions'." % region_name
                    region_name = raw_input('Enter the name of the region you would like to use: ')
                tries+=1
                if tries > 5:
                    print "*** Error, region you are requesting not available.  No changes made to regions."
                    return
        self.db_services.set_region(region_name)
        print "Region updated to ", region_name
        self.config.set('AWS Access', 'aws_region', region_name)
        if self.server.is_server_running() == 'yes':
            self.server_relaunch()

    def db_aws_list_instances(self):
        instances = self.db_services.get_db_instances()
        if not instances:
            print "There are no DB instances associated with your AWS account in region ", self.db_services.get_region()
        else:
            print "Here are the current DB instances associated with your AWS account in region ", self.db_services.get_region()
            for dbinst in instances:
                print '\t'+'-'*20
                print "\tInstance ID: " + dbinst.id
                print "\tStatus: " + dbinst.status

    def db_aws_delete_instance(self, instance_id):
        interactive = False
        if instance_id is None:
            interactive = True

        instances = self.db_services.get_db_instances()
        instance_list = [dbinst.id for dbinst in instances]

        if interactive:
            valid = False
            if len(instances)==0:
                print "There are no instances you can delete currently.  Use `db aws_create_instance` to make one."
                return
            print "Here are the available instances you can delete:"
            for inst in instances:
                print "\t ", inst.id, "(", inst.status, ")"
            while not valid:
                instance_id = raw_input('Enter the instance identity you would like to delete: ')
                res = self.db_services.validate_instance_id(instance_id)
                if (res == True):
                    valid = True
                else:
                    print res + " Try again, instance name not valid.  Check for typos."
                if instance_id in instance_list:
                    valid = True
                else:
                    valid = False
                    print "Try again, instance not present in this account.  Try again checking for typos."
        else:
            res = self.db_services.validate_instance_id(instance_id)
            if (res != True):
                print "*** Error, instance name either not valid.  Try again checking for typos."
                return
            if instance_id not in instance_list:
                print "*** Error, This instance not present in this account.  Try again checking for typos.  Run `db aws_list_instances` to see valid list."
                return

        r = raw_input("Deleting an instance will erase all your data associated with the database in that instance. Really quit? y or n: ")
        if r == 'y':
            res = self.db_services.delete_db_instance(instance_id)
            if res:
                print "AWS RDS database instance %s deleted.  Run `db aws_list_instances` for current status." % instance_id
            else:
                print "*** Error deleting database instance ", instance_id, ". It maybe because it is still being created, deleted, or is being backed up.  Run `db aws_list_instances` for current status."
        else:
            return

    def db_use_aws_instance(self, instance_id):
        # set your database info to use the current instance
        # configure a security zone for this based on your ip
        interactive = False
        if instance_id is None:
            interactive = True

        instances = self.db_services.get_db_instances()
        instance_list = [dbinst.id for dbinst in instances]

        if len(instances)==0:
            print "There are no instances in this region/account.  Use `db aws_create_instance` to make one first."
            return

        # show list of available instances, if there are none cancel immediately
        if interactive:
            valid = False
            print "Here are the available instances you have.  You can only use those listed as 'available':"
            for inst in instances:
                print "\t ", inst.id, "(", inst.status, ")"
            while not valid:
                instance_id = raw_input('Enter the instance identity you would like to use: ')
                res = self.db_services.validate_instance_id(instance_id)
                if (res == True):
                    valid = True
                else:
                    print res + " Try again, instance name not valid.  Check for typos."
                if instance_id in instance_list:
                    valid = True
                else:
                    valid = False
                    print "Try again, instance not present in this account.  Try again checking for typos."
        else:
            res = self.db_services.validate_instance_id(instance_id)
            if (res != True):
                print "*** Error, instance name either not valid.  Try again checking for typos."
                return
            if instance_id not in instance_list:
                print "*** Error, This instance not present in this account.  Try again checking for typos.  Run `db aws_list_instances` to see valid list."
                return

        r = raw_input("Switching your DB settings to use this instance.  Are you sure you want to do this? ")
        if r == 'y':
            # ask for password
            valid = False
            while not valid:
                password = raw_input('enter the master password for this instance: ')
                res = self.db_services.validate_instance_password(password)
                if res != True:
                    print "*** Error: password seems incorrect, doesn't conform to AWS rules.  Try again"
                else:
                    valid = True

            # get instance
            myinstance = self.db_services.get_db_instance_info(instance_id)
            if myinstance:
                # add security zone to this node to allow connections
                my_ip = self.web_services.get_my_ip()
                if not self.db_services.allow_access_to_instance(myinstance, my_ip):
                    print "*** Error authorizing your ip address to connect to server (%s)." % my_ip
                    return
                print "AWS RDS database instance %s selected." % instance_id

                # using regular sql commands list available database on this node
                try:
                    db_url = 'mysql://' + myinstance.master_username + ":" + password + "@" + myinstance.endpoint[0] + ":" + str(myinstance.endpoint[1])
                    engine = sa.create_engine(db_url, echo=False)
                    e = engine.connect().execute
                    db_names = e("show databases").fetchall()
                except:
                    print "***  Error connecting to instance.  Your password my be incorrect."
                    return
                existing_dbs = [db[0] for db in db_names if db not in [('information_schema',), ('innodb',), ('mysql',), ('performance_schema',)]]
                create_db=False
                if len(existing_dbs)==0:
                    valid = False
                    while not valid:
                        db_name = raw_input("No existing DBs in this instance.  Enter a new name to create one: ")
                        res = self.db_services.validate_instance_dbname(db_name)
                        if res == True:
                            valid = True
                        else:
                            print res + " Try again."
                    create_db=True
                else:
                    print "Here are the available database tables"
                    for db in existing_dbs:
                        print "\t" + db
                    valid = False
                    while not valid:
                        db_name = raw_input("Enter the name of the database you want to use or a new name to create a new one: ")
                        res = self.db_services.validate_instance_dbname(db_name)
                        if res == True:
                            valid = True
                        else:
                            print res + " Try again."
                    if db_name not in existing_dbs:
                        create_db=True
                if create_db:
                    try:
                        connection.execute("CREATE DATABASE %s;" % db_name)
                    except:
                        print "*** Error creating database %s on instance %s" % (db_name,instance_id)
                        return
                base_url = 'mysql://' + myinstance.master_username + ":" + password + "@" + myinstance.endpoint[0] + ":" + str(myinstance.endpoint[1]) + "/" + db_name
                self.config.set("Database Parameters", "database_url", base_url)
                print "Successfully set your current database (database_url) to \n\t%s" % base_url
                if self.server.is_server_running()=='maybe' or self.server.is_server_running()=='yes':
                    self.do_restart_server('')
            else:
                print "*** Error selecting database instance " + arg['<id>'] + ". Run `list_db_instances` for current status of instances, only `available` instances can be used.  Also your password may be incorrect."
        else:
            return


    def db_create_aws_db_instance(self, instid=None, size=None, username=None, password=None, dbname=None):
        interactive = False
        if instid is None:
            interactive = True

        if interactive:
            print '*************************************************'
            print 'Ok, here are the rules on creating instances:'
            print ''
            print 'instance id:'
            print '  Each instance needs an identifier.  This is the name'
            print '  of the virtual machine created for you on AWS.'
            print '  Rules are 1-63 alphanumeric characters, first must'
            print '  be a letter, must be unique to this AWS account.'
            print ''
            print 'size:'
            print '  The maximum size of you database in GB.  Enter an'
            print '  integer between 5-1024'
            print ''
            print 'master username:'
            print '  The username you will use to connect.  Rules are'
            print '  1-16 alphanumeric characters, first must be a letter,'
            print '  cannot be a reserved MySQL word/phrase'
            print ''
            print 'master password:'
            print '  Rules are 8-41 alphanumeric characters'
            print ''
            print 'database name:'
            print '  The name for the first database on this instance.  Rules are'
            print '  1-64 alphanumeric characters, cannot be a reserved MySQL word'
            print '*************************************************'
            print ''

        if interactive:
            valid = False
            while not valid:
                instid = raw_input('enter an identifier for the instance (see rules above): ')
                res = self.db_services.validate_instance_id(instid)
                if res == True:
                    valid = True
                else:
                    print res + " Try again."
        else:
            res = self.db_services.validate_instance_id(instid)
            if res is not True:
                print res
                return

        if interactive:
            valid = False
            while not valid:
                size = raw_input('size of db in GB (5-1024): ')
                res = self.db_services.validate_instance_size(size)
                if res == True:
                    valid = True
                else:
                    print res + " Try again."
        else:
            res = self.db_services.validate_instance_size(size)
            if res is not True:
                print res
                return

        if interactive:
            valid = False
            while not valid:
                username = raw_input('master username (see rules above): ')
                res = self.db_services.validate_instance_username(username)
                if res == True:
                    valid = True
                else:
                    print res + " Try again."
        else:
            res = self.db_services.validate_instance_username(username)
            if res is not True:
                print res
                return

        if interactive:
            valid = False
            while not valid:
                password = raw_input('master password (see rules above): ')
                res = self.db_services.validate_instance_password(password)
                if res == True:
                    valid = True
                else:
                    print res + " Try again."
        else:
            res = self.db_services.validate_instance_password(password)
            if res is not True:
                print res
                return

        if interactive:
            valid = False
            while not valid:
                dbname = raw_input('name for first database on this instance (see rules): ')
                res = self.db_services.validate_instance_dbname(dbname)
                if res == True:
                    valid = True
                else:
                    print res + " Try again."
        else:
            res = self.db_services.validate_instance_dbname(dbname)
            if res is not True:
                print res
                return

        options = {
            'id': instid,
            'size': size,
            'username': username,
            'password': password,
            'dbname': dbname
        }
        instance = self.db_services.create_db_instance(options)
        if not instance:
            print '*****************************'
            print '  Sorry there was an error creating db instance.'
        else:
            print '*****************************'
            print '  Creating AWS RDS MySQL Instance'
            print '    id: ', str(options['id'])
            print '    size: ', str(options['size']), " GB"
            print '    username: ', str(options['username'])
            print '    password: ', str(options['password'])
            print '    dbname: ',  str(options['dbname'])
            print '    type: MySQL/db.t1.micro'
            print '    ________________________'
            print ' Be sure to store this information in a safe place.'
            print ' Please wait 5-10 minutes while your database is created in the cloud.'
            print ' You can run \'db aws_list_instances\' to verify it was created (status'
            print ' will say \'available\' when it is ready'


    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    #  Basic shell commands
    #+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.+-+.
    @docopt_cmd
    def do_mode(self, arg):
        """
        Usage: mode
               mode <which>
        """
        if arg['<which>'] is None:
            if self.sandbox:
                arg['<which>'] = 'live'
            else:
                arg['<which>'] = 'sandbox'
        if arg['<which>'] == 'live':
            self.sandbox = False
            self.config.set('HIT Configuration', 'using_sandbox', False)
            self.amt_services.set_sandbox(False)
            self.tally_hits()
            print 'Entered ' + colorize('live', 'bold') + ' mode'
        else:
            self.sandbox = True
            self.config.set('HIT Configuration', 'using_sandbox', True)
            self.amt_services.set_sandbox(True)
            self.tally_hits()
            print 'Entered ' + colorize('sandbox', 'bold') + ' mode'

    def help_mode(self):
        with open(self.helpPath + 'mode.txt', 'r') as helpText:
            print helpText.read()

    @docopt_cmd
    def do_hit(self, arg):
        """
        Usage:
          hit create [<numWorkers> <reward> <duration>]
          hit extend <HITid> [--assignments <number>] [--expiration <minutes>]
          hit expire (--all | <HITid> ...)
          hit dispose (--all | <HITid> ...)
          hit list (all | active | reviewable)
          hit help
        """

        if arg['create']:
            self.hit_create(arg['<numWorkers>'], arg['<reward>'], arg['<duration>'])
        elif arg['extend']:
            self.hit_extend(arg['<HITid>'], arg['<number>'], arg['<minutes>'])
        elif arg['expire']:
            self.hit_expire(arg['--all'], arg['<HITid>'])
        elif arg['dispose']:
            self.hit_dispose(arg['--all'], arg['<HITid>'])
        elif arg['list']:
            self.hit_list(arg['all'], arg['active'], arg['reviewable'])
        else:
            self.help_hit()

    hit_commands = ('create', 'extend', 'expire', 'dispose', 'list')

    def complete_hit(self, text, line, begidx, endidx):
        return  [i for i in PsiturkShell.hit_commands if i.startswith(text)]

    def help_hit(self):
        with open(self.helpPath + 'hit.txt', 'r') as helpText:
            print helpText.read()


    @docopt_cmd
    def do_worker(self, arg):
        """
        Usage:
          worker approve (--hit <hit_id> | <assignment_id> ...)
          worker reject (--hit <hit_id> | <assignment_id> ...)
          worker bonus (--hit <hit_id> | <assignment_id> ...) (--auto | <amount>)
          worker list (submitted | approved | rejected | all) [--hit <hit_id>]
          worker help
        """
        if arg['approve']:
            self.worker_approve(arg['<hit_id>'], arg['<assignment_id>'])
        elif arg['reject']:
            self.worker_reject(arg['<hit_id>'], arg['<assignment_id>'])
        elif arg['list']:
            self.worker_list(arg['submitted'], arg['approved'], arg['rejected'], arg['all'], arg['<hit_id>'])
        elif arg['bonus']:
            self.worker_bonus(arg['<hit_id>'], arg['--auto'], arg['<amount>'], "", arg['<assignment_id>'])
        else:
            self.help_worker()

    worker_commands = ('approve', 'reject', 'list', 'help')

    def complete_worker(self, text, line, begidx, endidx):
        return  [i for i in PsiturkShell.worker_commands if i.startswith(text)]

    def help_worker(self):
        with open(self.helpPath + 'worker.txt', 'r') as helpText:
            print helpText.read()

    @docopt_cmd
    def do_amt(self, arg):
        """
        Usage:
          amt balance
          amt help
        """
        if arg['balance']:
            self.amt_balance()
        else:
            self.help_amt()

    amt_commands = ('balance', 'help')

    def complete_amt(self, text, line, begidx, endidx):
        return [i for i in PsiturkShell.amt_commands if i.startswith(text)]

    def help_amt(self):
        with open(self.helpPath + 'amt.txt', 'r') as helpText:
            print helpText.read()

    # modified version of standard cmd help which lists psiturk commands first
    def do_help(self, arg):
        if arg:
            try:
                func = getattr(self, 'help_' + arg)
            except AttributeError:
                try:
                    doc = getattr(self, 'do_' + arg).__doc__
                    if doc:
                        self.stdout.write("%s\n" % str(doc))
                        return
                except AttributeError:
                    pass
                self.stdout.write("%s\n" % str(self.nohelp % (arg,)))
                return
            func()
        else:
            # Modifications start here
            names = dir(PsiturkNetworkShell)
            superNames = dir(Cmd)
            newNames = [m for m in names if m not in superNames]
            help = {}
            cmds_psiTurk = []
            cmds_super = []
            for name in names:
                if name[:5] == 'help_':
                    help[name[5:]]=1
            names.sort()
            prevname = ''
            for name in names:
                if name[:3] == 'do_':
                    if name == prevname:
                        continue
                    prevname = name
                    cmd = name[3:]
                    if cmd in help:
                        del help[cmd]
                    if name in newNames:
                        cmds_psiTurk.append(cmd)
                    else:
                        cmds_super.append(cmd)
            self.stdout.write("%s\n" % str(self.doc_leader))
            self.print_topics(self.psiTurk_header, cmds_psiTurk, 15, 80)
            self.print_topics(self.misc_header, help.keys(), 15, 80)
            self.print_topics(self.super_header, cmds_super, 15, 80)

def run(cabinmode=False):
    sys.argv = [sys.argv[0]] # drop arguments which were already processed in command_line.py
    #opt = docopt(__doc__, sys.argv[1:])
    config = PsiturkConfig()
    config.load_config()
    server = control.ExperimentServerController(config)
    if cabinmode:
        shell = PsiturkShell(config, server)
        shell.check_offline_configuration()
    else:
        amt_services = MTurkServices(config.get('AWS Access', 'aws_access_key_id'), \
                                 config.get('AWS Access', 'aws_secret_access_key'), \
                                 config.getboolean('HIT Configuration','using_sandbox'))
        aws_rds_services = RDSServices(config.get('AWS Access', 'aws_access_key_id'), \
                                 config.get('AWS Access', 'aws_secret_access_key'),
                                 config.get('AWS Access', 'aws_region'))
        web_services = PsiturkOrgServices(config.get('Secure Ad Server','location'), config.get('Secure Ad Server', 'contact_email'))
        shell = PsiturkNetworkShell(config, amt_services, aws_rds_services, web_services, server)
    shell.cmdloop()
