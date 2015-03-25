import HydrusConstants as HC
import HydrusData
import ClientData
import HydrusGlobals

import ClientCaches
import collections
import gc
import hashlib
import httplib
import HydrusExceptions
import HydrusNetworking
import HydrusPubSub
import HydrusSessions
import HydrusServer
import HydrusTags
import HydrusThreading
import ClientConstants as CC
import ClientDB
import ClientGUI
import ClientGUIDialogs
import os
import random
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
import wx
import wx.richtext
from twisted.internet import reactor
from twisted.internet import defer

ID_ANIMATED_EVENT_TIMER = wx.NewId()
ID_MAINTENANCE_EVENT_TIMER = wx.NewId()

MAINTENANCE_PERIOD = 5 * 60

class Controller( wx.App ):
    
    def _CheckIfJustWokeFromSleep( self ):
        
        last_maintenance_time = self._timestamps[ 'last_maintenance_time' ]
        
        if last_maintenance_time == 0: return False
        
        # this tests if we probably just woke up from a sleep
        if HydrusData.GetNow() - last_maintenance_time > MAINTENANCE_PERIOD + ( 5 * 60 ): self._just_woke_from_sleep = True
        else: self._just_woke_from_sleep = False
        
    
    def _Read( self, action, *args, **kwargs ): return self._db.Read( action, HC.HIGH_PRIORITY, *args, **kwargs )
    
    def _Write( self, action, priority, synchronous, *args, **kwargs ): return self._db.Write( action, priority, synchronous, *args, **kwargs )
    
    def BackupDatabase( self ):
        
        with wx.DirDialog( self._gui, 'Select backup location.' ) as dlg:
            
            if dlg.ShowModal() == wx.ID_OK:
                
                path = dlg.GetPath()
                
                text = 'Are you sure "' + path + '" is the correct directory?'
                text += os.linesep * 2
                text += 'Everything already in that directory will be deleted before the backup starts.'
                text += os.linesep * 2
                text += 'The database will be locked while the backup occurs, which may lock up your gui as well.'
                
                with ClientGUIDialogs.DialogYesNo( self._gui, text ) as dlg_yn:
                    
                    if dlg_yn.ShowModal() == wx.ID_YES:
                        
                        self.Write( 'backup', path )
                        
                    
                
            
        
    
    def ClearCaches( self ):
        
        self._thumbnail_cache.Clear()
        self._fullscreen_image_cache.Clear()
        self._preview_image_cache.Clear()
        
    
    def Clipboard( self, data_type, data ):
        
        # need this cause can't do it in a non-gui thread
        
        if data_type == 'paths':
            
            paths = data
            
            if wx.TheClipboard.Open():
                
                data = wx.DataObjectComposite()
                
                file_data = wx.FileDataObject()
                
                for path in paths: file_data.AddFile( path )
                
                text_data = wx.TextDataObject( os.linesep.join( paths ) )
                
                data.Add( file_data, True )
                data.Add( text_data, False )
                
                wx.TheClipboard.SetData( data )
                
                wx.TheClipboard.Close()
                
            else: wx.MessageBox( 'Could not get permission to access the clipboard!' )
            
        elif data_type == 'text':
            
            text = data
            
            if wx.TheClipboard.Open():
                
                data = wx.TextDataObject( text )
                
                wx.TheClipboard.SetData( data )
                
                wx.TheClipboard.Close()
                
            else: wx.MessageBox( 'I could not get permission to access the clipboard.' )
            
        elif data_type == 'bmp':
            
            media = data
            
            image_container = wx.GetApp().GetFullscreenImageCache().GetImage( media )
            
            def THREADWait():
                
                # have to do this in thread, because the image rendered needs the wx event queue to render
                
                start_time = time.time()
                
                while not image_container.IsRendered():
                    
                    if time.time() - start_time > 15: raise Exception( 'The image did not render in fifteen seconds, so the attempt to copy it to the clipboard was abandoned.' )
                    
                    time.sleep( 0.1 )
                    
                
                wx.CallAfter( CopyToClipboard )
                
            
            def CopyToClipboard():
                
                if wx.TheClipboard.Open():
                    
                    hydrus_bmp = image_container.GetHydrusBitmap()
                    
                    wx_bmp = hydrus_bmp.GetWxBitmap()
                    
                    data = wx.BitmapDataObject( wx_bmp )
                    
                    wx.TheClipboard.SetData( data )
                    
                    wx.TheClipboard.Close()
                    
                else: wx.MessageBox( 'I could not get permission to access the clipboard.' )
                
            
            HydrusThreading.CallToThread( THREADWait )
            
        
    
    def CurrentlyIdle( self ):
        
        if HC.options[ 'idle_period' ] == 0: return False
        
        return HydrusData.GetNow() - self._timestamps[ 'last_user_action' ] > HC.options[ 'idle_period' ]
        
    
    def EventPubSub( self, event ):
        
        self._currently_doing_pubsub = True
        
        try: HydrusGlobals.pubsub.WXProcessQueueItem()
        finally: self._currently_doing_pubsub = False
        
    
    def GetDB( self ): return self._db
    
    def GetFullscreenImageCache( self ): return self._fullscreen_image_cache
    
    def GetGUI( self ): return self._gui
    
    def GetManager( self, manager_type ): return self._managers[ manager_type ]
    
    def GetPreviewImageCache( self ): return self._preview_image_cache
    
    def GetThumbnailCache( self ): return self._thumbnail_cache
    
    def InitCheckPassword( self ):
        
        while True:
            
            with wx.PasswordEntryDialog( None, 'Enter your password', 'Enter password' ) as dlg:
                
                if dlg.ShowModal() == wx.ID_OK:
                    
                    if hashlib.sha256( dlg.GetValue() ).digest() == HC.options[ 'password' ]: break
                    
                else: raise HydrusExceptions.PermissionException()
                
            
        
    
    def InitDB( self ):
        
        db_initialised = False
        
        while not db_initialised:
            
            try:
                
                self._db = ClientDB.DB()
                
                db_initialised = True
                
            except HydrusExceptions.DBAccessException as e:
                
                try: print( HydrusData.ToString( e ) )
                except: print( repr( HydrusData.ToString( e ) ) )
                
                message = 'This instance of the client had a problem connecting to the database, which probably means an old instance is still closing.'
                message += os.linesep * 2
                message += 'If the old instance does not close for a _very_ long time, you can usually safely force-close it from task manager.'
                
                with ClientGUIDialogs.DialogYesNo( None, message, 'There was a problem connecting to the database.', yes_label = 'wait a bit, then try again', no_label = 'forget it' ) as dlg:
                    
                    if dlg.ShowModal() == wx.ID_YES: time.sleep( 3 )
                    else: raise HydrusExceptions.PermissionException()
                    
                
            
        
        threading.Thread( target = self._db.MainLoop, name = 'Database Main Loop' ).start()
        
    
    def InitGUI( self ):
        
        self._managers = {}
        
        self._managers[ 'services' ] = ClientData.ServicesManager()
        
        self._managers[ 'hydrus_sessions' ] = HydrusSessions.HydrusSessionManagerClient()
        self._managers[ 'local_booru' ] = ClientCaches.LocalBooruCache()
        self._managers[ 'tag_censorship' ] = HydrusTags.TagCensorshipManager()
        self._managers[ 'tag_siblings' ] = HydrusTags.TagSiblingsManager()
        self._managers[ 'tag_parents' ] = HydrusTags.TagParentsManager()
        self._managers[ 'undo' ] = ClientData.UndoManager()
        self._managers[ 'web_sessions' ] = HydrusSessions.WebSessionManagerClient()
        
        self._fullscreen_image_cache = ClientCaches.RenderedImageCache( 'fullscreen' )
        self._preview_image_cache = ClientCaches.RenderedImageCache( 'preview' )
        
        self._thumbnail_cache = ClientCaches.ThumbnailCache()
        
        CC.GlobalBMPs.STATICInitialise()
        
        self._gui = ClientGUI.FrameGUI()
        
        HydrusGlobals.pubsub.sub( self, 'Clipboard', 'clipboard' )
        HydrusGlobals.pubsub.sub( self, 'RestartServer', 'restart_server' )
        HydrusGlobals.pubsub.sub( self, 'RestartBooru', 'restart_booru' )
        
        self.Bind( wx.EVT_TIMER, self.TIMEREventMaintenance, id = ID_MAINTENANCE_EVENT_TIMER )
        
        self._maintenance_event_timer = wx.Timer( self, ID_MAINTENANCE_EVENT_TIMER )
        self._maintenance_event_timer.Start( MAINTENANCE_PERIOD * 1000, wx.TIMER_CONTINUOUS )
        
        # this is because of some bug in wx C++ that doesn't add these by default
        wx.richtext.RichTextBuffer.AddHandler( wx.richtext.RichTextHTMLHandler() )
        wx.richtext.RichTextBuffer.AddHandler( wx.richtext.RichTextXMLHandler() )
        
        if HydrusGlobals.is_first_start: wx.CallAfter( self._gui.DoFirstStart )
        if HydrusGlobals.is_db_updated: wx.CallLater( 1, HydrusData.ShowText, 'The client has updated to version ' + HydrusData.ToString( HC.SOFTWARE_VERSION ) + '!' )
        
        self.RestartServer()
        self.RestartBooru()
        self._db.StartDaemons()
        
    
    def JustWokeFromSleep( self ):
        
        if not self._just_woke_from_sleep: self._CheckIfJustWokeFromSleep()
        
        return self._just_woke_from_sleep
        
    
    def MaintainDB( self ):
        
        gc.collect()
        
        now = HydrusData.GetNow()
        
        shutdown_timestamps = self.Read( 'shutdown_timestamps' )
        
        if HC.options[ 'maintenance_vacuum_period' ] != 0:
            
            if now - shutdown_timestamps[ CC.SHUTDOWN_TIMESTAMP_VACUUM ] > HC.options[ 'maintenance_vacuum_period' ]: self.Write( 'vacuum' )
            
        
        if HC.options[ 'maintenance_delete_orphans_period' ] != 0:
            
            if now - shutdown_timestamps[ CC.SHUTDOWN_TIMESTAMP_DELETE_ORPHANS ] > HC.options[ 'maintenance_delete_orphans_period' ]: self.Write( 'delete_orphans' )
            
        
        if now - self._timestamps[ 'last_service_info_cache_fatten' ] > 60 * 20:
            
            HydrusGlobals.pubsub.pub( 'set_splash_text', 'fattening service info' )
            
            services = self.GetManager( 'services' ).GetServices()
            
            for service in services:
                
                try: self.Read( 'service_info', service.GetServiceKey() )
                except: pass # sometimes this breaks when a service has just been removed and the client is closing, so ignore the error
                
            
            self._timestamps[ 'last_service_info_cache_fatten' ] = HydrusData.GetNow()
            
        
        HydrusGlobals.pubsub.pub( 'clear_closed_pages' )
        
    
    def OnInit( self ):
        
        self.SetAssertMode( wx.PYAPP_ASSERT_SUPPRESS )
        
        self._currently_doing_pubsub = False
        
        self._timestamps = collections.defaultdict( lambda: 0 )
        
        self._timestamps[ 'boot' ] = HydrusData.GetNow()
        
        self._just_woke_from_sleep = False
        
        self._local_service = None
        self._booru_service = None
        
        self.Bind( HydrusPubSub.EVT_PUBSUB, self.EventPubSub )
        
        try:
            
            splash = ClientGUI.FrameSplash( 'boot' )
            
            return True
            
        except:
            
            print( 'There was an error trying to start the splash screen!' )
            
            print( traceback.format_exc() )
            
            try: wx.CallAfter( splash.Destroy )
            except: pass
            
            return False
            
        
    
    def PrepStringForDisplay( self, text ):
        
        if HC.options[ 'gui_capitalisation' ]: return text
        else: return text.lower()
        
    
    def Read( self, action, *args, **kwargs ): return self._Read( action, *args, **kwargs )
    
    def ReadDaemon( self, action, *args, **kwargs ):
        
        result = self._Read( action, *args, **kwargs )
        
        time.sleep( 0.1 )
        
        return result
        
    
    def ResetIdleTimer( self ): self._timestamps[ 'last_user_action' ] = HydrusData.GetNow()
    
    def RestartBooru( self ):
        
        service = self.GetManager( 'services' ).GetService( CC.LOCAL_BOORU_SERVICE_KEY )
        
        info = service.GetInfo()
        
        port = info[ 'port' ]
        
        def TWISTEDRestartServer():
            
            def StartServer( *args, **kwargs ):
                
                try:
                    
                    connection = httplib.HTTPConnection( '127.0.0.1', port, timeout = 10 )
                    
                    try:
                        
                        connection.connect()
                        connection.close()
                        
                        text = 'The client\'s booru server could not start because something was already bound to port ' + HydrusData.ToString( port ) + '.'
                        text += os.linesep * 2
                        text += 'This usually means another hydrus client is already running and occupying that port. It could be a previous instantiation of this client that has yet to shut itself down.'
                        text += os.linesep * 2
                        text += 'You can change the port this client tries to host its local server on in services->manage services.'
                        
                        wx.CallLater( 1, HydrusData.ShowText, text )
                        
                    except:
                        
                        self._booru_service = reactor.listenTCP( port, HydrusServer.HydrusServiceBooru( CC.LOCAL_BOORU_SERVICE_KEY, HC.LOCAL_BOORU, 'This is the local booru.' ) )
                        
                        connection = httplib.HTTPConnection( '127.0.0.1', port, timeout = 10 )
                        
                        try:
                            
                            connection.connect()
                            connection.close()
                            
                        except:
                            
                            text = 'Tried to bind port ' + HydrusData.ToString( port ) + ' for the local booru, but it failed.'
                            
                            wx.CallLater( 1, HydrusData.ShowText, text )
                            
                        
                    
                except Exception as e: wx.CallAfter( HydrusData.ShowException, e )
                
            
            if self._booru_service is None: StartServer()
            else:
                
                deferred = defer.maybeDeferred( self._booru_service.stopListening )
                
                deferred.addCallback( StartServer )
                
            
        
        reactor.callFromThread( TWISTEDRestartServer )
        
    
    def RestartServer( self ):
        
        port = HC.options[ 'local_port' ]
        
        def TWISTEDRestartServer():
            
            def StartServer( *args, **kwargs ):
                
                try:
                    
                    connection = httplib.HTTPConnection( '127.0.0.1', port, timeout = 10 )
                    
                    try:
                        
                        connection.connect()
                        connection.close()
                        
                        text = 'The client\'s local server could not start because something was already bound to port ' + HydrusData.ToString( port ) + '.'
                        text += os.linesep * 2
                        text += 'This usually means another hydrus client is already running and occupying that port. It could be a previous instantiation of this client that has yet to shut itself down.'
                        text += os.linesep * 2
                        text += 'You can change the port this client tries to host its local server on in file->options.'
                        
                        wx.CallLater( 1, HydrusData.ShowText, text )
                        
                    except:
                        
                        self._local_service = reactor.listenTCP( port, HydrusServer.HydrusServiceLocal( CC.LOCAL_FILE_SERVICE_KEY, HC.LOCAL_FILE, 'This is the local file service.' ) )
                        
                        connection = httplib.HTTPConnection( '127.0.0.1', port, timeout = 10 )
                        
                        try:
                            
                            connection.connect()
                            connection.close()
                            
                        except:
                            
                            text = 'Tried to bind port ' + HydrusData.ToString( port ) + ' for the local server, but it failed.'
                            
                            wx.CallLater( 1, HydrusData.ShowText, text )
                            
                        
                    
                except Exception as e: wx.CallAfter( HydrusData.ShowException, e )
                
            
            if self._local_service is None: StartServer()
            else:
                
                deferred = defer.maybeDeferred( self._local_service.stopListening )
                
                deferred.addCallback( StartServer )
                
            
        
        reactor.callFromThread( TWISTEDRestartServer )
        
    
    def RestoreDatabase( self ):
        
        with wx.DirDialog( self._gui, 'Select backup location.' ) as dlg:
            
            if dlg.ShowModal() == wx.ID_OK:
                
                path = dlg.GetPath()
                
                text = 'Are you sure you want to restore a backup from "' + path + '"?'
                text += os.linesep * 2
                text += 'Everything in your current database will be deleted!'
                text += os.linesep * 2
                text += 'The gui will shut down, and then it will take a while to complete the restore. Once it is done, the client will restart.'
                
                with ClientGUIDialogs.DialogYesNo( self._gui, text ) as dlg_yn:
                    
                    if dlg_yn.ShowModal() == wx.ID_YES:
                        
                        self._gui.Hide()
                        
                        self._gui.Close()
                        
                        self._db.Shutdown()
                        
                        while not self._db.LoopIsFinished(): time.sleep( 0.1 )
                        
                        self._db.RestoreBackup( path )
                        
                        call_stuff = [ sys.executable ]
                        
                        call_stuff.extend( sys.argv )
                        
                        subprocess.Popen( call_stuff, shell = True )
                        
                    
                
            
        
    
    def StartFileQuery( self, query_key, search_context ): HydrusThreading.CallToThread( self.THREADDoFileQuery, query_key, search_context )
    
    def THREADDoFileQuery( self, query_key, search_context ):
        
        query_hash_ids = self.Read( 'file_query_ids', search_context )
        
        query_hash_ids = list( query_hash_ids )
        
        random.shuffle( query_hash_ids )
        
        limit = search_context.GetSystemPredicates().GetLimit()
        
        if limit is not None: query_hash_ids = query_hash_ids[ : limit ]
        
        service_key = search_context.GetFileServiceKey()
        
        include_current_tags = search_context.IncludeCurrentTags()
        
        media_results = []
        
        include_pending_tags = search_context.IncludePendingTags()
        
        i = 0
        
        base = 256
        
        while i < len( query_hash_ids ):
            
            if query_key.IsCancelled(): return
            
            if i == 0: ( last_i, i ) = ( 0, base )
            else: ( last_i, i ) = ( i, i + base )
            
            sub_query_hash_ids = query_hash_ids[ last_i : i ]
            
            more_media_results = self.Read( 'media_results_from_ids', service_key, sub_query_hash_ids )
            
            media_results.extend( more_media_results )
            
            HydrusGlobals.pubsub.pub( 'set_num_query_results', len( media_results ), len( query_hash_ids ) )
            
            self.WaitUntilGoodTimeToUseGUIThread()
            
        
        HydrusGlobals.pubsub.pub( 'file_query_done', query_key, media_results )
        
    
    def TIMEREventMaintenance( self, event ):
        
        sys.stdout.flush()
        sys.stderr.flush()
        
        self._CheckIfJustWokeFromSleep()
        
        self._timestamps[ 'last_maintenance_time' ] = HydrusData.GetNow()
        
        if not self._just_woke_from_sleep and self.CurrentlyIdle(): self.MaintainDB()
        
    
    def WaitUntilGoodTimeToUseGUIThread( self ):
        
        while True:
            
            if HydrusGlobals.shutdown: raise Exception( 'Client shutting down!' )
            elif HydrusGlobals.pubsub.NoJobsQueued() and not self._currently_doing_pubsub: return
            else: time.sleep( 0.00001 )
            
        
    
    def Write( self, action, *args, **kwargs ):
        
        if action == 'content_updates': self._managers[ 'undo' ].AddCommand( 'content_updates', *args, **kwargs )
        
        return self._Write( action, HC.HIGH_PRIORITY, False, *args, **kwargs )
        
    
    def WriteSynchronous( self, action, *args, **kwargs ):
        
        result = self._Write( action, HC.LOW_PRIORITY, True, *args, **kwargs )
        
        time.sleep( 0.1 )
        
        return result
        
    