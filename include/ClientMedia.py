import bisect
import collections
import ClientConstants as CC
import ClientData
import ClientFiles
import ClientRatings
import ClientSearch
import HydrusConstants as HC
import HydrusTags
import os
import random
import time
import traceback
import wx
import HydrusData
import HydrusFileHandling
import HydrusExceptions
import HydrusGlobals as HG
import HydrusSerialisable
import itertools

def FlattenMedia( media_list ):
    
    flat_media = []
    
    for media in media_list:
        
        if media.IsCollection():
            
            flat_media.extend( media.GetFlatMedia() )
            
        else:
            
            flat_media.append( media )
            
        
    
    return flat_media
    
def GetDuplicateComparisonStatements( shown_media, comparison_media ):
    
    statements = []
    score = 0
    
    # size
    
    s_size = shown_media.GetSize()
    c_size = comparison_media.GetSize()
    
    size_ratio = float( s_size ) / float( c_size )
    
    if size_ratio > 2.0:
        
        statements.append( 'This has a much larger filesize.' )
        
        score += 2
        
    elif size_ratio > 1.05:
        
        statements.append( 'This has a larger filesize.' )
        
        score += 0.5
        
    elif size_ratio < 0.5:
        
        statements.append( 'This has a much smaller filesize.' )
        
        score -= 2
        
    elif size_ratio < 0.95:
        
        statements.append( 'This has a smaller filesize.' )
        
        score -= 0.5
        
    
    # higher/same res
    
    s_resolution = shown_media.GetResolution()
    c_resolution = comparison_media.GetResolution()
    
    if s_resolution is not None and c_resolution is not None:
        
        ( s_w, s_h ) = shown_media.GetResolution()
        ( c_w, c_h ) = comparison_media.GetResolution()
        
        resolution_ratio = float( s_w * s_h ) / float( c_w * c_h )
        
        if resolution_ratio == 1.0:
            
            if s_resolution != c_resolution:
                
                statements.append( 'The files have the same number of pixels but different resolution.' )
                
            
        elif resolution_ratio > 2.0:
            
            statements.append( 'This has much higher resolution.' )
            
            score += 2
            
        elif resolution_ratio > 1.0:
            
            statements.append( 'This has higher resolution.' )
            
            score += 1
            
        elif resolution_ratio < 0.5:
            
            statements.append( 'This has much lower resolution.' )
            
            score -= 2
            
        elif resolution_ratio < 1.0:
            
            statements.append( 'This has lower resolution.' )
            
            score -= 1
            
        
    
    # same/diff mime
    
    s_mime = shown_media.GetMime()
    c_mime = comparison_media.GetMime()
    
    if s_mime != c_mime:
        
        statements.append( 'This is ' + HC.mime_string_lookup[ s_mime ] + ', the other is ' + HC.mime_string_lookup[ c_mime ] + '.' )
        
    
    # more tags
    
    s_num_tags = len( shown_media.GetTagsManager().GetCurrent() )
    c_num_tags = len( comparison_media.GetTagsManager().GetCurrent() )
    
    if s_num_tags > 0 and c_num_tags > 0:
        
        if s_num_tags > c_num_tags:
            
            statements.append( 'This has more tags.' )
            
            score += 1
            
        elif s_num_tags < c_num_tags:
            
            statements.append( 'This has fewer tags.' )
            
            score += 1
            
        
    elif s_num_tags > 0:
        
        statements.append( 'This has tags, the other does not.' )
        
    elif c_num_tags > 0:
        
        statements.append( 'This has no tags, the other does.' )
        
    
    # older
    
    s_ts = shown_media.GetLocationsManager().GetTimestamp( CC.COMBINED_LOCAL_FILE_SERVICE_KEY )
    c_ts = comparison_media.GetLocationsManager().GetTimestamp( CC.COMBINED_LOCAL_FILE_SERVICE_KEY )
    
    if s_ts is not None and c_ts is not None:
        
        if s_ts < c_ts - 86400 * 30:
            
            statements.append( 'This is older.' )
            
            score += 0.5
            
        elif c_ts < s_ts - 86400 * 30:
            
            statements.append( 'This is newer.' )
            
            score -= 0.5
            
        
    
    return ( statements, score )
    
def MergeTagsManagers( tags_managers ):
    
    def CurrentAndPendingFilter( items ):
        
        for ( service_key, statuses_to_tags ) in items:
            
            filtered = { status : tags for ( status, tags ) in statuses_to_tags.items() if status in ( HC.CONTENT_STATUS_CURRENT, HC.CONTENT_STATUS_PENDING ) }
            
            yield ( service_key, filtered )
            
        
    
    # [[( service_key, statuses_to_tags )]]
    s_k_s_t_t_tupled = ( CurrentAndPendingFilter( tags_manager.GetServiceKeysToStatusesToTags().items() ) for tags_manager in tags_managers )
    
    # [(service_key, statuses_to_tags)]
    flattened_s_k_s_t_t = itertools.chain.from_iterable( s_k_s_t_t_tupled )
    
    # service_key : [ statuses_to_tags ]
    s_k_s_t_t_dict = HydrusData.BuildKeyToListDict( flattened_s_k_s_t_t )
    
    # now let's merge so we have service_key : statuses_to_tags
    
    merged_service_keys_to_statuses_to_tags = collections.defaultdict( HydrusData.default_dict_set )
    
    for ( service_key, several_statuses_to_tags ) in s_k_s_t_t_dict.items():
        
        # [[( status, tags )]]
        s_t_t_tupled = ( s_t_t.items() for s_t_t in several_statuses_to_tags )
        
        # [( status, tags )]
        flattened_s_t_t = itertools.chain.from_iterable( s_t_t_tupled )
        
        statuses_to_tags = HydrusData.default_dict_set()
        
        for ( status, tags ) in flattened_s_t_t: statuses_to_tags[ status ].update( tags )
        
        merged_service_keys_to_statuses_to_tags[ service_key ] = statuses_to_tags
        
    
    return TagsManager( merged_service_keys_to_statuses_to_tags )
    
class DuplicatesManager( object ):
    
    def __init__( self, service_keys_to_dupe_statuses_to_counts ):
        
        self._service_keys_to_dupe_statuses_to_counts = service_keys_to_dupe_statuses_to_counts
        
    
    def Duplicate( self ):
        
        service_keys_to_dupe_statuses_to_counts = collections.defaultdict( collections.Counter )
        
        return DuplicatesManager( service_keys_to_dupe_statuses_to_counts )
        
    
    def GetDupeStatusesToCounts( self, service_key ):
        
        return self._service_keys_to_dupe_statuses_to_counts[ service_key ]
        
    
class FileInfoManager( object ):
    
    def __init__( self, hash, size = None, mime = None, width = None, height = None, duration = None, num_frames = None, num_words = None ):
        
        if mime is None:
            
            mime = HC.APPLICATION_UNKNOWN
            
        
        self._hash = hash
        self._size = size
        self._mime = mime
        self._width = width
        self._height = height
        self._duration = duration
        self._num_frames = num_frames
        self._num_words = num_words
        
    
    def Duplicate( self ):
        
        return FileInfoManager( self._hash, self._size, self._mime, self._width, self._height, self._duration, self._num_frames, self._num_words )
        
    
    def GetDuration( self ):
        
        return self._duration
        
    
    def GetHash( self ):
        
        return self._hash
        
    
    def GetMime( self ):
        
        return self._mime
        
    
    def GetNumFrames( self ):
        
        return self._num_frames
        
    
    def GetNumWords( self ):
        
        return self._num_words
        
    
    def GetResolution( self ):
        
        return ( self._width, self._height )
        
    
    def GetSize( self ):
        
        return self._size
        
    
    def ToTuple( self ):
        
        return ( self._hash, self._size, self._mime, self._width, self._height, self._duration, self._num_frames, self._num_words )
        
    
class LocationsManager( object ):
    
    LOCAL_LOCATIONS = { CC.LOCAL_FILE_SERVICE_KEY, CC.TRASH_SERVICE_KEY, CC.COMBINED_LOCAL_FILE_SERVICE_KEY }
    
    def __init__( self, current, deleted, pending, petitioned, inbox = False, urls = None, service_keys_to_filenames = None, current_to_timestamps = None ):
        
        self._current = current
        self._deleted = deleted
        self._pending = pending
        self._petitioned = petitioned
        
        self._inbox = inbox
        
        if urls is None:
            
            urls = set()
            
        
        self._urls = urls
        
        if service_keys_to_filenames is None:
            
            service_keys_to_filenames = {}
            
        
        self._service_keys_to_filenames = service_keys_to_filenames
        
        if current_to_timestamps is None:
            
            current_to_timestamps = {}
            
        
        self._current_to_timestamps = current_to_timestamps
        
    
    def DeletePending( self, service_key ):
        
        self._pending.discard( service_key )
        self._petitioned.discard( service_key )
        
    
    def Duplicate( self ):
        
        current = set( self._current )
        deleted = set( self._deleted )
        pending = set( self._pending )
        petitioned = set( self._petitioned )
        urls = set( self._urls )
        service_keys_to_filenames = dict( self._service_keys_to_filenames )
        current_to_timestamps = dict( self._current_to_timestamps )
        
        return LocationsManager( current, deleted, pending, petitioned, self._inbox, urls, service_keys_to_filenames, current_to_timestamps )
        
    
    def GetCDPP( self ): return ( self._current, self._deleted, self._pending, self._petitioned )
    
    def GetCurrent( self ): return self._current
    def GetCurrentRemote( self ):
        
        return self._current - self.LOCAL_LOCATIONS
        
    
    def GetDeleted( self ): return self._deleted
    def GetDeletedRemote( self ):
        
        return self._deleted - self.LOCAL_LOCATIONS
        
    
    def GetInbox( self ):
        
        return self._inbox
        
    
    def GetPending( self ): return self._pending
    def GetPendingRemote( self ):
        
        return self._pending - self.LOCAL_LOCATIONS
        
    
    def GetPetitioned( self ): return self._petitioned
    def GetPetitionedRemote( self ):
        
        return self._petitioned - self.LOCAL_LOCATIONS
        
    
    def GetRemoteLocationStrings( self ):
    
        current = self.GetCurrentRemote()
        pending = self.GetPendingRemote()
        petitioned = self.GetPetitionedRemote()
        
        remote_services = HG.client_controller.services_manager.GetServices( ( HC.FILE_REPOSITORY, HC.IPFS ) )
        
        remote_services = list( remote_services )
        
        def key( s ):
            
            return s.GetName()
            
        
        remote_services.sort( key = key )
        
        remote_service_strings = []
        
        for remote_service in remote_services:
            
            name = remote_service.GetName()
            service_key = remote_service.GetServiceKey()
            
            if service_key in pending:
                
                remote_service_strings.append( name + ' (+)' )
                
            elif service_key in current:
                
                if service_key in petitioned:
                    
                    remote_service_strings.append( name + ' (-)' )
                    
                else:
                    
                    remote_service_strings.append( name )
                    
                
            
        
        return remote_service_strings
        
    
    def GetTimestamp( self, service_key ):
        
        if service_key in self._current_to_timestamps:
            
            return self._current_to_timestamps[ service_key ]
            
        else:
            
            return None
            
        
    
    def GetURLs( self ):
        
        return self._urls
        
    
    def IsDownloading( self ):
        
        return CC.COMBINED_LOCAL_FILE_SERVICE_KEY in self._pending
        
    
    def IsLocal( self ):
        
        return CC.COMBINED_LOCAL_FILE_SERVICE_KEY in self._current
        
    
    def IsRemote( self ):
        
        return CC.COMBINED_LOCAL_FILE_SERVICE_KEY not in self._current
        
    
    def IsTrashed( self ):
        
        return CC.TRASH_SERVICE_KEY in self._current
        
    
    def ProcessContentUpdate( self, service_key, content_update ):
        
        ( data_type, action, row ) = content_update.ToTuple()
        
        if data_type == HC.CONTENT_TYPE_FILES:
            
            if action == HC.CONTENT_UPDATE_ARCHIVE:
                
                self._inbox = False
                
            elif action == HC.CONTENT_UPDATE_INBOX:
                
                self._inbox = True
                
            elif action == HC.CONTENT_UPDATE_ADD:
                
                self._current.add( service_key )
                
                self._deleted.discard( service_key )
                self._pending.discard( service_key )
                
                if service_key == CC.LOCAL_FILE_SERVICE_KEY:
                    
                    self._current.discard( CC.TRASH_SERVICE_KEY )
                    self._pending.discard( CC.COMBINED_LOCAL_FILE_SERVICE_KEY )
                    
                    if CC.COMBINED_LOCAL_FILE_SERVICE_KEY not in self._current:
                        
                        self._inbox = True
                        
                        self._current.add( CC.COMBINED_LOCAL_FILE_SERVICE_KEY )
                        
                        self._current_to_timestamps[ CC.COMBINED_LOCAL_FILE_SERVICE_KEY ] = HydrusData.GetNow()
                        
                    
                
                self._current_to_timestamps[ service_key ] = HydrusData.GetNow()
                
            elif action == HC.CONTENT_UPDATE_DELETE:
                
                self._deleted.add( service_key )
                
                self._current.discard( service_key )
                self._petitioned.discard( service_key )
                
                if service_key == CC.LOCAL_FILE_SERVICE_KEY:
                    
                    self._current.add( CC.TRASH_SERVICE_KEY )
                    
                    self._current_to_timestamps[ CC.TRASH_SERVICE_KEY ] = HydrusData.GetNow()
                    
                elif service_key == CC.TRASH_SERVICE_KEY:
                    
                    self._inbox = False
                    
                    self._current.discard( CC.COMBINED_LOCAL_FILE_SERVICE_KEY )
                    
                
            elif action == HC.CONTENT_UPDATE_UNDELETE:
                
                self._current.discard( CC.TRASH_SERVICE_KEY )
                
                self._deleted.discard( CC.LOCAL_FILE_SERVICE_KEY )
                self._current.add( CC.LOCAL_FILE_SERVICE_KEY )
                
            elif action == HC.CONTENT_UPDATE_PEND:
                
                if service_key not in self._current: self._pending.add( service_key )
                
            elif action == HC.CONTENT_UPDATE_PETITION:
                
                if service_key not in self._deleted: self._petitioned.add( service_key )
                
            elif action == HC.CONTENT_UPDATE_RESCIND_PEND:
                
                self._pending.discard( service_key )
                
            elif action == HC.CONTENT_UPDATE_RESCIND_PETITION:
                
                self._petitioned.discard( service_key )
                
            
        elif data_type == HC.CONTENT_TYPE_URLS:
            
            if action == HC.CONTENT_UPDATE_ADD:
                
                ( hash, urls ) = row
                
                self._urls.update( urls )
                
            elif action == HC.CONTENT_UPDATE_DELETE:
                
                ( hash, urls ) = row
                
                self._urls.difference_update( urls )
                
                
            
        
    
    def ResetService( self, service_key ):
        
        self._current.discard( service_key )
        self._pending.discard( service_key )
        self._deleted.discard( service_key )
        self._petitioned.discard( service_key )
        
    
    def ShouldHaveThumbnail( self ):
        
        return len( self._current ) > 0
        
    
class Media( object ):
    
    def __init__( self ):
        
        self._id = HydrusData.GenerateKey()
        self._id_hash = self._id.__hash__()
        
    
    def __eq__( self, other ): return self.__hash__() == other.__hash__()
    
    def __hash__( self ): return self._id_hash
    
    def __ne__( self, other ): return self.__hash__() != other.__hash__()
    
class MediaList( object ):
    
    def __init__( self, file_service_key, media_results ):
        
        self._file_service_key = file_service_key
        
        self._hashes = set()
        
        self._hashes_to_singleton_media = {}
        self._hashes_to_collected_media = {}
        
        self._media_sort = MediaSort( ( 'system', CC.SORT_FILES_BY_FILESIZE ), CC.SORT_ASC )
        self._collect_by = []
        
        self._collect_map_singletons = {}
        self._collect_map_collected = {}
        
        self._sorted_media = SortedList( [ self._GenerateMediaSingleton( media_result ) for media_result in media_results ] )
        
        self._singleton_media = set( self._sorted_media )
        self._collected_media = set()
        
        self._RecalcHashes()
        
    
    def __len__( self ):
        
        return len( self._singleton_media ) + sum( map( len, self._collected_media ) )
        
    
    def _CalculateCollectionKeysToMedias( self, collect_by, medias ):
        
        keys_to_medias = collections.defaultdict( list )
        
        namespaces_to_collect_by = [ data for ( collect_by_type, data ) in collect_by if collect_by_type == 'namespace' ]
        ratings_to_collect_by = [ data for ( collect_by_type, data ) in collect_by if collect_by_type == 'rating' ]
        
        services_manager = HG.client_controller.services_manager
        
        for media in medias:
            
            if len( namespaces_to_collect_by ) > 0:
                
                namespace_key = media.GetTagsManager().GetNamespaceSlice( namespaces_to_collect_by )
                
            else:
                
                namespace_key = None
                
            
            if len( ratings_to_collect_by ) > 0:
                
                rating_key = media.GetRatingsManager().GetRatingSlice( ratings_to_collect_by )
                
            else:
                
                rating_key = None
                
            
            keys_to_medias[ ( namespace_key, rating_key ) ].append( media )
            
        
        return keys_to_medias
        
    
    def _GenerateMediaCollection( self, media_results ):
        
        return MediaCollection( self._file_service_key, media_results )
        
    
    def _GenerateMediaSingleton( self, media_result ):
        
        return MediaSingleton( media_result )
        
    
    def _GetFirst( self ): return self._sorted_media[ 0 ]
    
    def _GetLast( self ): return self._sorted_media[ -1 ]
    
    def _GetMedia( self, hashes, discriminator = None ):
        
        if hashes.isdisjoint( self._hashes ):
            
            return []
            
        
        medias = []
        
        if discriminator is None or discriminator == 'singletons':
            
            medias.extend( ( self._hashes_to_singleton_media[ hash ] for hash in hashes if hash in self._hashes_to_singleton_media ) )
            
        
        if discriminator is None or discriminator == 'collections':
            
            medias.extend( { self._hashes_to_collected_media[ hash ] for hash in hashes if hash in self._hashes_to_collected_media } )
            
        
        return medias
        
        '''
        if discriminator is None:
            
            medias = self._sorted_media
            
        elif discriminator == 'singletons':
            
            medias = self._singleton_media
            
        elif discriminator == 'collections':
            
            medias = self._collected_media
            
        
        return [ media for media in medias if media.HasAnyOfTheseHashes( hashes ) ]
        '''
    
    def _GetNext( self, media ):
        
        if media is None:
            
            return None
            
        
        next_index = self._sorted_media.index( media ) + 1
        
        if next_index == len( self._sorted_media ):
            
            return self._GetFirst()
            
        else:
            
            return self._sorted_media[ next_index ]
            
        
    
    def _GetPrevious( self, media ):
        
        if media is None: return None
        
        previous_index = self._sorted_media.index( media ) - 1
        
        if previous_index == -1: return self._GetLast()
        else: return self._sorted_media[ previous_index ]
        
    
    def _HasHashes( self, hashes ):
        
        for hash in hashes:
            
            if hash in self._hashes:
                
                return True
                
            
        
        return False
        
    
    def _RecalcHashes( self ):
        
        self._hashes = set()
        
        self._hashes_to_singleton_media = {}
        self._hashes_to_collected_media = {}
        
        for media in self._collected_media:
            
            hashes = media.GetHashes()
            
            self._hashes.update( hashes )
            
            for hash in hashes:
                
                self._hashes_to_collected_media[ hash ] = media
                
            
        
        for media in self._singleton_media:
            
            hash = media.GetHash()
            
            self._hashes.add( hash )
            
            self._hashes_to_singleton_media[ hash ] = media
            
        
    
    def _RemoveMediaByHashes( self, hashes ):
        
        if not isinstance( hashes, set ):
            
            hashes = set( hashes )
            
        
        affected_singleton_media = self._GetMedia( hashes, discriminator = 'singletons' )
        
        for media in self._collected_media:
            
            media._RemoveMediaByHashes( hashes )
            
        
        affected_collected_media = [ media for media in self._collected_media if media.HasNoMedia() ]
        
        self._RemoveMediaDirectly( affected_singleton_media, affected_collected_media )
        
    
    def _RemoveMediaDirectly( self, singleton_media, collected_media ):
        
        if not isinstance( singleton_media, set ):
            
            singleton_media = set( singleton_media )
            
        
        if not isinstance( collected_media, set ):
            
            collected_media = set( collected_media )
            
        
        self._singleton_media.difference_update( singleton_media )
        self._collected_media.difference_update( collected_media )
        
        keys_to_remove = [ key for ( key, media ) in self._collect_map_singletons if media in singleton_media ]
        
        for key in keys_to_remove:
            
            del self._collect_map_singletons[ key ]
            
        
        keys_to_remove = [ key for ( key, media ) in self._collect_map_collected if media in collected_media ]
        
        for key in keys_to_remove:
            
            del self._collect_map_collected[ key ]
            
        
        self._sorted_media.remove_items( singleton_media.union( collected_media ) )
        
        self._RecalcHashes()
        
    
    def AddMedia( self, new_media, append = True ):
        
        if append:
            
            for media in new_media:
                
                hash = media.GetHash()
                
                self._hashes.add( hash )
                
                self._hashes_to_singleton_media[ hash ] = media
                
            
            self._singleton_media.update( new_media )
            self._sorted_media.append_items( new_media )
            
        else:
            
            if self._collect_by is not None:
                
                keys_to_medias = self._CalculateCollectionKeysToMedias( self._collect_by, new_media )
                
                new_media = []
                
                for ( key, medias ) in keys_to_medias.items():
                    
                    if key in self._collect_map_singletons:
                        
                        singleton_media = self._collect_map_singletons[ key ]
                        
                        self._sorted_media.remove_items( singleton_media )
                        self._singleton_media.discard( singleton_media )
                        del self._collect_map_singletons[ key ]
                        
                        medias.append( singleton_media )
                        
                        collected_media = self._GenerateMediaCollection( [ media.GetMediaResult() for media in medias ] )
                        
                        collected_media.Sort( self._media_sort )
                        
                        self._collected_media.add( collected_media )
                        self._collect_map_collected[ key ] = collected_media
                        
                        new_media.append( collected_media )
                        
                    elif key in self._collect_map_collected:
                        
                        collected_media = self._collect_map_collected[ key ]
                        
                        self._sorted_media.remove_items( collected_media )
                        
                        collected_media.AddMedia( medias )
                        
                        collected_media.Sort( self._media_sort )
                        
                        new_media.append( collected_media )
                        
                    elif len( medias ) == 1:
                        
                        ( singleton_media, ) = medias
                        
                        self._singleton_media.add( singleton_media )
                        self._collect_map_singletons[ key ] = singleton_media
                        
                    else:
                        
                        collected_media = self._GenerateMediaCollection( [ media.GetMediaResult() for media in medias ] )
                        
                        collected_media.Sort( self._media_sort )
                        
                        self._collected_media.add( collected_media )
                        self._collect_map_collected[ key ] = collected_media
                        
                        new_media.append( collected_media )
                        
                    
                
            
            self._sorted_media.insert_items( new_media )
            
            self._RecalcHashes()
            
        
        return new_media
        
    
    def Collect( self, collect_by = None ):
        
        if collect_by == None:
            
            collect_by = self._collect_by
            
        
        self._collect_by = collect_by
        
        for media in self._collected_media:
            
            self._singleton_media.update( [ self._GenerateMediaSingleton( media_result ) for media_result in media.GenerateMediaResults() ] )
            
        
        self._collected_media = set()
        
        self._collect_map_singletons = {}
        self._collect_map_collected = {}
        
        if len( collect_by ) > 0:
            
            keys_to_medias = self._CalculateCollectionKeysToMedias( collect_by, self._singleton_media )
            
            self._collect_map_singletons = { key : medias[0] for ( key, medias ) in keys_to_medias.items() if len( medias ) == 1 }
            self._collect_map_collected = { key : self._GenerateMediaCollection( [ media.GetMediaResult() for media in medias ] ) for ( key, medias ) in keys_to_medias.items() if len( medias ) > 1 }
            
            self._singleton_media = set( self._collect_map_singletons.values() )
            self._collected_media = set( self._collect_map_collected.values() )
            
        
        self._sorted_media = SortedList( list( self._singleton_media ) + list( self._collected_media ) )
        
        self._RecalcHashes()
        
    
    def DeletePending( self, service_key ):
        
        for media in self._collected_media: media.DeletePending( service_key )
        
    
    def GenerateMediaResults( self, has_location = None, discriminant = None, selected_media = None, unrated = None, for_media_viewer = False ):
        
        media_results = []
        
        for media in self._sorted_media:
            
            if has_location is not None:
                
                locations_manager = media.GetLocationsManager()
                
                if has_location not in locations_manager.GetCurrent():
                    
                    continue
                    
                
            
            if selected_media is not None and media not in selected_media:
                
                continue
                
            
            if media.IsCollection():
                
                media_results.extend( media.GenerateMediaResults( has_location = has_location, discriminant = discriminant, selected_media = selected_media, unrated = unrated, for_media_viewer = True ) )
                
            else:
                
                if discriminant is not None:
                    
                    locations_manager = media.GetLocationsManager()
                    
                    if discriminant == CC.DISCRIMINANT_INBOX:
                        
                        p = media.HasInbox()
                        
                    elif discriminant == CC.DISCRIMINANT_ARCHIVE:
                        
                        p = not media.HasInbox()
                        
                    elif discriminant == CC.DISCRIMINANT_LOCAL:
                        
                        p = locations_manager.IsLocal()
                        
                    elif discriminant == CC.DISCRIMINANT_LOCAL_BUT_NOT_IN_TRASH:
                        
                        p = locations_manager.IsLocal() and not locations_manager.IsTrashed()
                        
                    elif discriminant == CC.DISCRIMINANT_NOT_LOCAL:
                        
                        p = not locations_manager.IsLocal()
                        
                    elif discriminant == CC.DISCRIMINANT_DOWNLOADING:
                        
                        p = locations_manager.IsDownloading()
                        
                    
                    if not p:
                        
                        continue
                        
                    
                
                if unrated is not None:
                    
                    ratings_manager = media.GetRatingsManager()
                    
                    if ratings_manager.GetRating( unrated ) is not None:
                        
                        continue
                        
                    
                
                if for_media_viewer:
                    
                    new_options = HG.client_controller.GetNewOptions()
                    
                    media_show_action = new_options.GetMediaShowAction( media.GetMime() )
                    
                    if media_show_action in ( CC.MEDIA_VIEWER_ACTION_DO_NOT_SHOW_ON_ACTIVATION_OPEN_EXTERNALLY, CC.MEDIA_VIEWER_ACTION_DO_NOT_SHOW ):
                        
                        continue
                        
                    
                
                media_results.append( media.GetMediaResult() )
                
            
        
        return media_results
        
    
    def GetFirst( self ):
        
        return self._GetFirst()
        
    
    def GetFlatMedia( self ):
        
        flat_media = []
        
        for media in self._sorted_media:
            
            if media.IsCollection():
                
                flat_media.extend( media.GetFlatMedia() )
                
            else:
                
                flat_media.append( media )
                
            
        
        return flat_media
        
    
    def GetLast( self ):
        
        return self._GetLast()
        
    
    def GetMediaIndex( self, media ):
        
        return self._sorted_media.index( media )
        
    
    def GetNext( self, media ):
        
        return self._GetNext( media )
        
    
    def GetNumFiles( self ):
        
        return len( self._hashes )
        
    
    def GetPrevious( self, media ):
        
        return self._GetPrevious( media )
        
    
    def GetSortedMedia( self ):
        
        return self._sorted_media
        
    
    def HasAnyOfTheseHashes( self, hashes ):
        
        return not hashes.isdisjoint( self._hashes )
        
    
    def HasMedia( self, media ):
        
        if media is None:
            
            return False
            
        
        if media in self._singleton_media:
            
            return True
            
        elif media in self._collected_media:
            
            return True
            
        else:
            
            for media_collection in self._collected_media:
                
                if media_collection.HasMedia( media ):
                    
                    return True
                    
                
            
        
        return False
        
    
    def HasNoMedia( self ): return len( self._sorted_media ) == 0
    
    def ProcessContentUpdate( self, service_key, content_update ):
        
        ( data_type, action, row ) = content_update.ToTuple()
        
        hashes = content_update.GetHashes()
        
        for media in self._GetMedia( hashes, 'collections' ):
            
            media.ProcessContentUpdate( service_key, content_update )
            
        
        if data_type == HC.CONTENT_TYPE_FILES:
            
            if action == HC.CONTENT_UPDATE_DELETE:
                
                local_file_domains = HG.client_controller.services_manager.GetServiceKeys( ( HC.LOCAL_FILE_DOMAIN, ) )
                
                non_trash_local_file_services = list( local_file_domains ) + [ CC.COMBINED_LOCAL_FILE_SERVICE_KEY ]
                
                local_file_services = list( non_trash_local_file_services ) + [ CC.TRASH_SERVICE_KEY ]
                
                deleted_from_trash_and_local_view = service_key == CC.TRASH_SERVICE_KEY and self._file_service_key in local_file_services
                
                trashed_and_non_trash_local_view = HC.options[ 'remove_trashed_files' ] and service_key in non_trash_local_file_services and self._file_service_key in non_trash_local_file_services
                
                deleted_from_repo_and_repo_view = service_key not in local_file_services and self._file_service_key == service_key
                
                if deleted_from_trash_and_local_view or trashed_and_non_trash_local_view or deleted_from_repo_and_repo_view:
                    
                    self._RemoveMediaByHashes( hashes )
                    
                
            
        
    
    def ProcessContentUpdates( self, service_keys_to_content_updates ):
        
        for ( service_key, content_updates ) in service_keys_to_content_updates.items():
            
            for content_update in content_updates:
                
                self.ProcessContentUpdate( service_key, content_update )
                
            
        
    
    def ProcessServiceUpdates( self, service_keys_to_service_updates ):
        
        for ( service_key, service_updates ) in service_keys_to_service_updates.items():
            
            for service_update in service_updates:
                
                ( action, row ) = service_update.ToTuple()
                
                if action == HC.SERVICE_UPDATE_DELETE_PENDING:
                    
                    self.DeletePending( service_key )
                    
                elif action == HC.SERVICE_UPDATE_RESET:
                    
                    self.ResetService( service_key )
                    
                
            
        
    
    def ResetService( self, service_key ):
        
        if service_key == self._file_service_key:
            
            self._RemoveMediaDirectly( self._singleton_media, self._collected_media )
            
        else:
            
            for media in self._collected_media: media.ResetService( service_key )
            
        
    
    def Sort( self, media_sort = None ):
        
        for media in self._collected_media:
            
            media.Sort( media_sort )
            
        
        if media_sort is None:
            
            media_sort = self._media_sort
            
        
        self._media_sort = media_sort
        
        media_sort_fallback = HG.client_controller.GetNewOptions().GetFallbackSort()
        
        ( sort_key, reverse ) = media_sort_fallback.GetSortKeyAndReverse( self._file_service_key )
        
        self._sorted_media.sort( sort_key, reverse = reverse )
        
        # this is a stable sort, so the fallback order above will remain for equal items
        
        ( sort_key, reverse ) = self._media_sort.GetSortKeyAndReverse( self._file_service_key )
        
        self._sorted_media.sort( sort_key = sort_key, reverse = reverse )
        
    
class ListeningMediaList( MediaList ):
    
    def __init__( self, file_service_key, media_results ):
        
        MediaList.__init__( self, file_service_key, media_results )
        
        self._file_query_result = ClientSearch.FileQueryResult( media_results )
        
        HG.client_controller.sub( self, 'ProcessContentUpdates', 'content_updates_gui' )
        HG.client_controller.sub( self, 'ProcessServiceUpdates', 'service_updates_gui' )
        
    
    def AddMediaResults( self, media_results, append = True ):
        
        self._file_query_result.AddMediaResults( media_results )
        
        new_media = []
        
        for media_result in media_results:
            
            hash = media_result.GetHash()
            
            if hash in self._hashes:
                
                continue
                
            
            new_media.append( self._GenerateMediaSingleton( media_result ) )
            
        
        self.AddMedia( new_media, append = append )
        
        return new_media
        
    
class MediaCollection( MediaList, Media ):
    
    def __init__( self, file_service_key, media_results ):
        
        Media.__init__( self )
        MediaList.__init__( self, file_service_key, media_results )
        
        self._archive = True
        self._inbox = False
        
        self._size = 0
        self._size_definite = True
        
        self._width = None
        self._height = None
        self._duration = None
        self._num_frames = None
        self._num_words = None
        self._tags_manager = None
        self._locations_manager = None
        
        self._RecalcInternals()
        
    
    def _RecalcInternals( self ):
        
        self._RecalcHashes()
        
        self._archive = True in ( media.HasArchive() for media in self._sorted_media )
        self._inbox = True in ( media.HasInbox() for media in self._sorted_media )
        
        self._size = sum( [ media.GetSize() for media in self._sorted_media ] )
        self._size_definite = not False in ( media.IsSizeDefinite() for media in self._sorted_media )
        
        duration_sum = sum( [ media.GetDuration() for media in self._sorted_media if media.HasDuration() ] )
        
        if duration_sum > 0: self._duration = duration_sum
        else: self._duration = None
        
        tags_managers = [ m.GetTagsManager() for m in self._sorted_media ]
        
        self._tags_manager = MergeTagsManagers( tags_managers )
        
        # horrible compromise
        if len( self._sorted_media ) > 0:
            
            self._ratings_manager = self._sorted_media[0].GetRatingsManager()
            
        else:
            
            self._ratings_manager = ClientRatings.RatingsManager( {} )
            
        
        all_locations_managers = [ media.GetLocationsManager() for media in self._sorted_media ]
        
        current = HydrusData.IntelligentMassIntersect( [ locations_manager.GetCurrent() for locations_manager in all_locations_managers ] )
        deleted = HydrusData.IntelligentMassIntersect( [ locations_manager.GetDeleted() for locations_manager in all_locations_managers ] )
        pending = HydrusData.IntelligentMassIntersect( [ locations_manager.GetPending() for locations_manager in all_locations_managers ] )
        petitioned = HydrusData.IntelligentMassIntersect( [ locations_manager.GetPetitioned() for locations_manager in all_locations_managers ] )
        
        self._locations_manager = LocationsManager( current, deleted, pending, petitioned )
        
    
    def AddMedia( self, new_media, append = True ):
        
        MediaList.AddMedia( self, new_media, append = True )
        
        self._RecalcInternals()
        
    
    def DeletePending( self, service_key ):
        
        MediaList.DeletePending( self, service_key )
        
        self._RecalcInternals()
        
    
    def GetDisplayMedia( self ): return self._GetFirst().GetDisplayMedia()
    
    def GetDuration( self ): return self._duration
    
    def GetHash( self ): return self.GetDisplayMedia().GetHash()
    
    def GetHashes( self, has_location = None, discriminant = None, not_uploaded_to = None, ordered = False ):
        
        if has_location is None and discriminant is None and not_uploaded_to is None and not ordered:
            
            return self._hashes
            
        else:
            
            if ordered:
                
                result = []
                
                for media in self._sorted_media: result.extend( media.GetHashes( has_location, discriminant, not_uploaded_to, ordered ) )
                
            else:
                
                result = set()
                
                for media in self._sorted_media: result.update( media.GetHashes( has_location, discriminant, not_uploaded_to, ordered ) )
                
            
            return result
            
        
    
    def GetLocationsManager( self ): return self._locations_manager
    
    def GetMime( self ): return HC.APPLICATION_HYDRUS_CLIENT_COLLECTION
    
    def GetNumFiles( self ):
        
        return len( self._hashes )
        
    
    def GetNumInbox( self ): return sum( ( media.GetNumInbox() for media in self._sorted_media ) )
    
    def GetNumFrames( self ): return sum( ( media.GetNumFrames() for media in self._sorted_media ) )
    
    def GetNumWords( self ): return sum( ( media.GetNumWords() for media in self._sorted_media ) )
    
    def GetPrettyInfoLines( self ):
        
        size = HydrusData.ConvertIntToBytes( self._size )
        
        mime = HC.mime_string_lookup[ HC.APPLICATION_HYDRUS_CLIENT_COLLECTION ]
        
        info_string = size + ' ' + mime
        
        info_string += ' (' + HydrusData.ConvertIntToPrettyString( self.GetNumFiles() ) + ' files)'
        
        return [ info_string ]
        
    
    def GetRatingsManager( self ):
        
        return self._ratings_manager
        
    
    def GetResolution( self ): return ( self._width, self._height )
    
    def GetSingletonsTagsManagers( self ):
        
        tags_managers = [ m.GetTagsManager() for m in self._singleton_media ] 
        
        for m in self._collected_media: tags_managers.extend( m.GetSingletonsTagsManagers() )
        
        return tags_managers
        
    
    def GetSize( self ): return self._size
    
    def GetTagsManager( self ): return self._tags_manager
    
    def GetTimestamp( self, service_key ): return None
    
    def HasArchive( self ): return self._archive
    
    def HasDuration( self ): return self._duration is not None
    
    def HasImages( self ): return True in ( media.HasImages() for media in self._sorted_media )
    
    def HasInbox( self ): return self._inbox
    
    def IsCollection( self ): return True
    
    def IsImage( self ): return False
    
    def IsNoisy( self ): return self.GetDisplayMedia().GetMime() in HC.NOISY_MIMES
    
    def IsSizeDefinite( self ): return self._size_definite
    
    def ProcessContentUpdate( self, service_key, content_update ):
        
        MediaList.ProcessContentUpdate( self, service_key, content_update )
        
        self._RecalcInternals()
        
    
    def ResetService( self, service_key ):
        
        MediaList.ResetService( self, service_key )
        
        self._RecalcInternals()
        
    
class MediaSingleton( Media ):
    
    def __init__( self, media_result ):
        
        Media.__init__( self )
        
        self._media_result = media_result
        
    
    def Duplicate( self ):
        
        return MediaSingleton( self._media_result.Duplicate() )
        
    
    def GetDisplayMedia( self ):
        
        return self
        
    
    def GetDuration( self ):
        
        return self._media_result.GetDuration()
        
    
    def GetHash( self ):
        
        return self._media_result.GetHash()
        
    
    def GetHashes( self, has_location = None, discriminant = None, not_uploaded_to = None, ordered = False ):
        
        if discriminant is not None:
            
            inbox = self._media_result.GetInbox()
            
            locations_manager = self._media_result.GetLocationsManager()
            
            if discriminant == CC.DISCRIMINANT_INBOX:
                
                p = inbox
                
            elif discriminant == CC.DISCRIMINANT_ARCHIVE:
                
                p = not inbox
                
            elif discriminant == CC.DISCRIMINANT_LOCAL:
                
                p = locations_manager.IsLocal()
                
            elif discriminant == CC.DISCRIMINANT_LOCAL_BUT_NOT_IN_TRASH:
                
                p = locations_manager.IsLocal() and not locations_manager.IsTrashed()
                
            elif discriminant == CC.DISCRIMINANT_NOT_LOCAL:
                
                p = not locations_manager.IsLocal()
                
            elif discriminant == CC.DISCRIMINANT_DOWNLOADING:
                
                p = locations_manager.IsDownloading()
                
            
            if not p:
                
                if ordered:
                    
                    return []
                    
                else:
                    
                    return set()
                    
                
            
        
        if has_location is not None:
            
            locations_manager = self._media_result.GetLocationsManager()
            
            if has_location not in locations_manager.GetCurrent():
                
                if ordered:
                    
                    return []
                    
                else:
                    
                    return set()
                    
                
            
        
        if not_uploaded_to is not None:
            
            locations_manager = self._media_result.GetLocationsManager()
            
            if not_uploaded_to in locations_manager.GetCurrentRemote():
                
                if ordered:
                    
                    return []
                    
                else:
                    
                    return set()
                    
                
            
        
        if ordered:
            
            return [ self._media_result.GetHash() ]
            
        else:
            
            return { self._media_result.GetHash() }
            
        
    
    def GetLocationsManager( self ): return self._media_result.GetLocationsManager()
    
    def GetMediaResult( self ): return self._media_result
    
    def GetMime( self ): return self._media_result.GetMime()
    
    def GetNumFiles( self ): return 1
    
    def GetNumFrames( self ): return self._media_result.GetNumFrames()
    
    def GetNumInbox( self ):
        
        if self.HasInbox(): return 1
        else: return 0
        
    
    def GetNumWords( self ): return self._media_result.GetNumWords()
    
    def GetTimestamp( self, service_key ):
        
        return self._media_result.GetLocationsManager().GetTimestamp( service_key )
        
    
    def GetPrettyInfoLines( self ):
        
        file_info_manager = self._media_result.GetFileInfoManager()
        locations_manager = self._media_result.GetLocationsManager()
        
        ( hash, size, mime, width, height, duration, num_frames, num_words ) = file_info_manager.ToTuple()
        
        info_string = HydrusData.ConvertIntToBytes( size ) + ' ' + HC.mime_string_lookup[ mime ]
        
        if width is not None and height is not None: info_string += ' (' + HydrusData.ConvertIntToPrettyString( width ) + 'x' + HydrusData.ConvertIntToPrettyString( height ) + ')'
        
        if duration is not None: info_string += ', ' + HydrusData.ConvertMillisecondsToPrettyTime( duration )
        
        if num_frames is not None: info_string += ' (' + HydrusData.ConvertIntToPrettyString( num_frames ) + ' frames)'
        
        if num_words is not None: info_string += ' (' + HydrusData.ConvertIntToPrettyString( num_words ) + ' words)'
        
        lines = [ info_string ]
        
        locations_manager = self._media_result.GetLocationsManager()
        
        current_service_keys = locations_manager.GetCurrent()
        
        if CC.COMBINED_LOCAL_FILE_SERVICE_KEY in current_service_keys:
            
            timestamp = locations_manager.GetTimestamp( CC.COMBINED_LOCAL_FILE_SERVICE_KEY )
            
            lines.append( 'imported ' + HydrusData.ConvertTimestampToPrettyAgo( timestamp ) + ' ago' )
            
        
        if CC.TRASH_SERVICE_KEY in current_service_keys:
            
            timestamp = locations_manager.GetTimestamp( CC.TRASH_SERVICE_KEY )
            
            lines.append( 'trashed ' + HydrusData.ConvertTimestampToPrettyAgo( timestamp ) + ' ago' )
            
        
        for service_key in current_service_keys:
            
            if service_key in ( CC.COMBINED_LOCAL_FILE_SERVICE_KEY, CC.LOCAL_FILE_SERVICE_KEY, CC.TRASH_SERVICE_KEY ):
                
                continue
                
            
            timestamp = locations_manager.GetTimestamp( service_key )
            
            service = HG.client_controller.services_manager.GetService( service_key )
            
            service_type = service.GetServiceType()
            
            if service_type == HC.IPFS:
                
                status = 'pinned '
                
            else:
                
                status = 'uploaded '
                
            
            lines.append( status + 'to ' + service.GetName() + ' ' + HydrusData.ConvertTimestampToPrettyAgo( timestamp ) + ' ago' )
            
        
        return lines
        
    
    def GetRatingsManager( self ): return self._media_result.GetRatingsManager()
    
    def GetResolution( self ):
        
        ( width, height ) = self._media_result.GetResolution()
        
        if width is None: return ( 0, 0 )
        else: return ( width, height )
        
    
    def GetSize( self ):
        
        size = self._media_result.GetSize()
        
        if size is None: return 0
        else: return size
        
    
    def GetTagsManager( self ): return self._media_result.GetTagsManager()
    
    def GetTitleString( self ):
        
        title_string = ''
        
        siblings_manager = HG.client_controller.GetManager( 'tag_siblings' )
        
        namespaces = self._media_result.GetTagsManager().GetCombinedNamespaces( ( 'creator', 'series', 'title', 'volume', 'chapter', 'page' ) )
        
        creators = namespaces[ 'creator' ]
        series = namespaces[ 'series' ]
        titles = namespaces[ 'title' ]
        volumes = namespaces[ 'volume' ]
        chapters = namespaces[ 'chapter' ]
        pages = namespaces[ 'page' ]
        
        if len( creators ) > 0:
            
            title_string_append = ', '.join( creators )
            
            if len( title_string ) > 0: title_string += ' - ' + title_string_append
            else: title_string = title_string_append
            
        
        if len( series ) > 0:
            
            title_string_append = ', '.join( series )
            
            if len( title_string ) > 0: title_string += ' - ' + title_string_append
            else: title_string = title_string_append
            
        
        if len( titles ) > 0:
            
            title_string_append = ', '.join( titles )
            
            if len( title_string ) > 0: title_string += ' - ' + title_string_append
            else: title_string = title_string_append
            
        
        if len( volumes ) > 0:
            
            if len( volumes ) == 1:
                
                ( volume, ) = volumes
                
                title_string_append = 'volume ' + HydrusData.ToUnicode( volume )
                
            else:
                
                volumes_sorted = HydrusTags.SortNumericTags( volumes )
                
                title_string_append = 'volumes ' + HydrusData.ToUnicode( volumes_sorted[0] ) + '-' + HydrusData.ToUnicode( volumes_sorted[-1] )
                
            
            if len( title_string ) > 0: title_string += ' - ' + title_string_append
            else: title_string = title_string_append
            
        
        if len( chapters ) > 0:
            
            if len( chapters ) == 1:
                
                ( chapter, ) = chapters
                
                title_string_append = 'chapter ' + HydrusData.ToUnicode( chapter )
                
            else:
                
                chapters_sorted = HydrusTags.SortNumericTags( chapters )
                
                title_string_append = 'chapters ' + HydrusData.ToUnicode( chapters_sorted[0] ) + '-' + HydrusData.ToUnicode( chapters_sorted[-1] )
                
            
            if len( title_string ) > 0: title_string += ' - ' + title_string_append
            else: title_string = title_string_append
            
        
        if len( pages ) > 0:
            
            if len( pages ) == 1:
                
                ( page, ) = pages
                
                title_string_append = 'page ' + HydrusData.ToUnicode( page )
                
            else:
                
                pages_sorted = HydrusTags.SortNumericTags( pages )
                
                title_string_append = 'pages ' + HydrusData.ToUnicode( pages_sorted[0] ) + '-' + HydrusData.ToUnicode( pages_sorted[-1] )
                
            
            if len( title_string ) > 0: title_string += ' - ' + title_string_append
            else: title_string = title_string_append
            
        
        return title_string
        
    
    def HasAnyOfTheseHashes( self, hashes ):
        
        return self._media_result.GetHash() in hashes
        
    
    def HasArchive( self ): return not self._media_result.GetInbox()
    
    def HasDuration( self ): return self._media_result.GetDuration() is not None and self._media_result.GetNumFrames() > 1
    
    def HasImages( self ): return self.IsImage()
    
    def HasInbox( self ): return self._media_result.GetInbox()
    
    def IsCollection( self ): return False
    
    def IsImage( self ): return self._media_result.GetMime() in HC.IMAGES
    
    def IsNoisy( self ): return self._media_result.GetMime() in HC.NOISY_MIMES
    
    def IsSizeDefinite( self ): return self._media_result.GetSize() is not None

class MediaResult( object ):
    
    def __init__( self, file_info_manager, tags_manager, locations_manager, ratings_manager ):
        
        self._file_info_manager = file_info_manager
        self._tags_manager = tags_manager
        self._locations_manager = locations_manager
        self._ratings_manager = ratings_manager
        
    
    def DeletePending( self, service_key ):
        
        service = HG.client_controller.services_manager.GetService( service_key )
        
        service_type = service.GetServiceType()
        
        if service_type in HC.TAG_SERVICES:
            
            self._tags_manager.DeletePending( service_key )
            
        elif service_type in HC.FILE_SERVICES:
            
            self._locations_manager.DeletePending( service_key )
            
        
    
    def Duplicate( self ):
        
        file_info_manager = self._file_info_manager.Duplicate()
        tags_manager = self._tags_manager.Duplicate()
        locations_manager = self._locations_manager.Duplicate()
        ratings_manager = self._ratings_manager.Duplicate()
        
        return MediaResult( file_info_manager, tags_manager, locations_manager, ratings_manager )
        
    
    def GetDuration( self ):
        
        return self._file_info_manager.GetDuration()
        
    
    def GetFileInfoManager( self ):
        
        return self._file_info_manager
        
    
    def GetHash( self ):
        
        return self._file_info_manager.GetHash()
        
    
    def GetInbox( self ):
        
        return self._locations_manager.GetInbox()
        
    
    def GetLocationsManager( self ):
        
        return self._locations_manager
        
    
    def GetMime( self ):
        
        return self._file_info_manager.GetMime()
        
    
    def GetNumFrames( self ):
        
        return self._file_info_manager.GetNumFrames()
        
    
    def GetNumWords( self ):
        
        return self._file_info_manager.GetNumWords()
        
    
    def GetRatingsManager( self ):
        
        return self._ratings_manager
        
    
    def GetResolution( self ):
        
        return self._file_info_manager.GetResolution()
        
    
    def GetSize( self ):
        
        return self._file_info_manager.GetSize()
        
    
    def GetTagsManager( self ):
        
        return self._tags_manager
        
    
    def ProcessContentUpdate( self, service_key, content_update ):
        
        ( data_type, action, row ) = content_update.ToTuple()
        
        service = HG.client_controller.services_manager.GetService( service_key )
        
        service_type = service.GetServiceType()
        
        if service_type in HC.TAG_SERVICES:
            
            self._tags_manager.ProcessContentUpdate( service_key, content_update )
            
        elif service_type in HC.FILE_SERVICES:
            
            self._locations_manager.ProcessContentUpdate( service_key, content_update )
            
        elif service_type in HC.RATINGS_SERVICES:
            
            self._ratings_manager.ProcessContentUpdate( service_key, content_update )
            
        
    
    def ResetService( self, service_key ):
        
        self._tags_manager.ResetService( service_key )
        self._locations_manager.ResetService( service_key )
        
    
    def ToTuple( self ):
        
        return ( self._file_info_manager, self._tags_manager, self._locations_manager, self._ratings_manager )
        
    
class MediaSort( HydrusSerialisable.SerialisableBase ):
    
    SERIALISABLE_TYPE = HydrusSerialisable.SERIALISABLE_TYPE_MEDIA_SORT
    SERIALISABLE_NAME = 'Media Sort'
    SERIALISABLE_VERSION = 1
    
    def __init__( self, sort_type = None, sort_asc = None ):
        
        if sort_type is None:
            
            sort_type = ( 'system', CC.SORT_FILES_BY_FILESIZE )
            
        
        if sort_asc is None:
            
            sort_asc = CC.SORT_ASC
            
        
        self.sort_type = sort_type
        self.sort_asc = sort_asc
        
    
    def _GetSerialisableInfo( self ):
        
        ( sort_metatype, sort_data ) = self.sort_type
        
        if sort_metatype == 'system':
            
            serialisable_sort_data = sort_data
            
        elif sort_metatype == 'namespaces':
            
            serialisable_sort_data = sort_data
            
        elif sort_metatype == 'rating':
            
            service_key = sort_data
            
            serialisable_sort_data = service_key.encode( 'hex' )
            
        
        return ( sort_metatype, serialisable_sort_data, self.sort_asc )
        
    
    def _InitialiseFromSerialisableInfo( self, serialisable_info ):
        
        ( sort_metatype, serialisable_sort_data, self.sort_asc ) = serialisable_info
        
        if sort_metatype == 'system':
            
            sort_data = serialisable_sort_data
            
        elif sort_metatype == 'namespaces':
            
            sort_data = tuple( serialisable_sort_data )
            
        elif sort_metatype == 'rating':
            
            sort_data = serialisable_sort_data.decode( 'hex' )
            
        
        self.sort_type = ( sort_metatype, sort_data )
        
    
    def CanAsc( self ):
        
        ( sort_metatype, sort_data ) = self.sort_type
        
        if sort_metatype == 'system':
            
            if sort_data in ( CC.SORT_FILES_BY_MIME, CC.SORT_FILES_BY_RANDOM ):
                
                return False
                
            
        elif sort_metatype == 'namespaces':
            
            return False
            
        
        return True
        
    
    def GetSortKeyAndReverse( self, file_service_key ):
        
        reverse = False
        
        ( sort_metadata, sort_data ) = self.sort_type
        
        def deal_with_none( x ):
            
            if x is None: return -1
            else: return x
            
        
        if sort_metadata == 'system':
            
            if sort_data == CC.SORT_FILES_BY_RANDOM:
                
                def sort_key( x ):
                    
                    return random.random()
                    
                
            elif sort_data == CC.SORT_FILES_BY_FILESIZE:
                
                def sort_key( x ):
                    
                    return deal_with_none( x.GetSize() )
                    
                
            elif sort_data == CC.SORT_FILES_BY_DURATION:
                
                def sort_key( x ):
                    
                    return deal_with_none( x.GetDuration() )
                    
                
            elif sort_data == CC.SORT_FILES_BY_IMPORT_TIME:
                
                file_service = HG.client_controller.services_manager.GetService( file_service_key )
                
                file_service_type = file_service.GetServiceType()
                
                if file_service_type == HC.LOCAL_FILE_DOMAIN:
                    
                    file_service_key = CC.COMBINED_LOCAL_FILE_SERVICE_KEY
                    
                
                def sort_key( x ):
                    
                    return deal_with_none( x.GetTimestamp( file_service_key ) )
                    
                
            elif sort_data == CC.SORT_FILES_BY_HEIGHT:
                
                def sort_key( x ):
                    
                    return deal_with_none( x.GetResolution()[1] )
                    
                
            elif sort_data == CC.SORT_FILES_BY_WIDTH:
                
                def sort_key( x ):
                    
                    return deal_with_none( x.GetResolution()[0] )
                    
                
            elif sort_data == CC.SORT_FILES_BY_RATIO:
                
                def sort_key( x ):
                    
                    ( width, height ) = x.GetResolution()
                    
                    if width is None or height is None or width == 0 or height == 0:
                        
                        return -1
                        
                    else:
                        
                        return float( width ) / float( height )
                        
                    
                
            elif sort_data == CC.SORT_FILES_BY_NUM_PIXELS:
                
                def sort_key( x ):
                    
                    ( width, height ) = x.GetResolution()
                    
                    if width is None or height is None:
                        
                        return -1
                        
                    else:
                        
                        return width * height
                        
                    
                
            elif sort_data == CC.SORT_FILES_BY_NUM_TAGS:
                
                def sort_key( x ):
                    
                    tags_manager = x.GetTagsManager()
                    
                    return( len( tags_manager.GetCurrent() ) + len( tags_manager.GetPending() ) )
                    
                
            elif sort_data == CC.SORT_FILES_BY_MIME:
                
                def sort_key( x ):
                    
                    return x.GetMime()
                    
                
            
        elif sort_metadata == 'namespaces':
            
            namespaces = sort_data
            
            def sort_key( x ):
                
                x_tags_manager = x.GetTagsManager()
                
                return [ x_tags_manager.GetComparableNamespaceSlice( ( namespace, ) ) for namespace in namespaces ]
                
            
        elif sort_metadata == 'rating':
            
            service_key = sort_data
            
            def sort_key( x ):
                
                x_ratings_manager = x.GetRatingsManager()
                
                rating = deal_with_none( x_ratings_manager.GetRating( service_key ) )
                
                return rating
                
            
        
        return ( sort_key, self.sort_asc )
        
    
    def GetSortTypeString( self ):
        
        ( sort_metatype, sort_data ) = self.sort_type
        
        sort_string = 'sort by '
        
        if sort_metatype == 'system':
            
            sort_string_lookup = {}
            
            sort_string_lookup[ CC.SORT_FILES_BY_FILESIZE ] = 'filesize'
            sort_string_lookup[ CC.SORT_FILES_BY_DURATION ] = 'duration'
            sort_string_lookup[ CC.SORT_FILES_BY_IMPORT_TIME ] = 'time imported'
            sort_string_lookup[ CC.SORT_FILES_BY_MIME ] = 'mime'
            sort_string_lookup[ CC.SORT_FILES_BY_RANDOM ] = 'random'
            sort_string_lookup[ CC.SORT_FILES_BY_WIDTH ] = 'width'
            sort_string_lookup[ CC.SORT_FILES_BY_HEIGHT ] = 'height'
            sort_string_lookup[ CC.SORT_FILES_BY_RATIO ] = 'resolution ratio'
            sort_string_lookup[ CC.SORT_FILES_BY_NUM_PIXELS ] = 'number of pixels'
            sort_string_lookup[ CC.SORT_FILES_BY_NUM_TAGS ] = 'number of tags'
            
            sort_string += sort_string_lookup[ sort_data ]
            
        elif sort_metatype == 'namespaces':
            
            namespaces = sort_data
            
            sort_string += '-'.join( namespaces )
            
        elif sort_metatype == 'rating':
            
            service_key = sort_data
            
            service = HG.client_controller.services_manager.GetService( service_key )
            
            sort_string += service.GetName()
            
        
        return sort_string
        
    
    def GetSortAscStrings( self ):
        
        ( sort_metatype, sort_data ) = self.sort_type
        
        if sort_metatype == 'system':
            
            sort_string_lookup = {}
            
            sort_string_lookup[ CC.SORT_FILES_BY_FILESIZE ] = ( 'smallest first', 'largest first' )
            sort_string_lookup[ CC.SORT_FILES_BY_DURATION ] = ( 'shortest first', 'longest first' )
            sort_string_lookup[ CC.SORT_FILES_BY_IMPORT_TIME ] = ( 'oldest first', 'newest first' )
            sort_string_lookup[ CC.SORT_FILES_BY_MIME ] = ( 'mime', 'mime' )
            sort_string_lookup[ CC.SORT_FILES_BY_RANDOM ] = ( 'random', 'random' )
            sort_string_lookup[ CC.SORT_FILES_BY_WIDTH ] = ( 'slimmest first', 'widest first' )
            sort_string_lookup[ CC.SORT_FILES_BY_HEIGHT ] = ( 'shortest first', 'tallest first' )
            sort_string_lookup[ CC.SORT_FILES_BY_RATIO ] = ( 'tallest first', 'widest first' )
            sort_string_lookup[ CC.SORT_FILES_BY_NUM_PIXELS ] = ( 'ascending', 'descending' )
            sort_string_lookup[ CC.SORT_FILES_BY_NUM_TAGS ] = ( 'ascending', 'descending' )
            
            return sort_string_lookup[ sort_data ]
            
        else:
            
            return ( 'ascending', 'descending' )
            
        
    
HydrusSerialisable.SERIALISABLE_TYPES_TO_OBJECT_TYPES[ HydrusSerialisable.SERIALISABLE_TYPE_MEDIA_SORT ] = MediaSort

class SortedList( object ):
    
    def __init__( self, initial_items = None ):
        
        if initial_items is None:
            
            initial_items = []
            
        
        self._sort_key = None
        self._sort_reverse = False
        
        self._sorted_list = list( initial_items )
        
        self._items_to_indices = None
        
    
    def __contains__( self, item ):
        
        return self._items_to_indices.__contains__( item )
        
    
    def __getitem__( self, value ):
        
        return self._sorted_list.__getitem__( value )
        
    
    def __iter__( self ):
        
        return iter( self._sorted_list )
        
    
    def __len__( self ):
        
        return len( self._sorted_list )
        
    
    def _DirtyIndices( self ):
        
        self._items_to_indices = None
        
    
    def _RecalcIndices( self ):
        
        self._items_to_indices = { item : index for ( index, item ) in enumerate( self._sorted_list ) }
        
    
    def append_items( self, items ):
        
        if self._items_to_indices is None:
            
            self._RecalcIndices()
            
        
        for ( i, item ) in enumerate( items, start = len( self._sorted_list ) ):
            
            self._items_to_indices[ item ] = i
            
        
        self._sorted_list.extend( items )
        
    
    def index( self, item ):
        
        if self._items_to_indices is None:
            
            self._RecalcIndices()
            
        
        try:
            
            result = self._items_to_indices[ item ]
            
        except KeyError:
            
            raise HydrusExceptions.DataMissing()
            
        
        return result
        
    
    def insert_items( self, items ):
        
        self.append_items( items )
        
        self.sort()
        
    
    def remove_items( self, items ):
        
        deletee_indices = [ self.index( item ) for item in items ]
        
        deletee_indices.sort()
        
        deletee_indices.reverse()
        
        for index in deletee_indices:
            
            del self._sorted_list[ index ]
            
        
        self._DirtyIndices()
        
    
    def sort( self, sort_key = None, reverse = False ):
        
        if sort_key is None:
            
            sort_key = self._sort_key
            reverse = self._sort_reverse
            
        else:
            
            self._sort_key = sort_key
            self._sort_reverse = reverse
            
        
        self._sorted_list.sort( key = sort_key, reverse = reverse )
        
        self._DirtyIndices()
        
    
class TagsManagerSimple( object ):
    
    def __init__( self, service_keys_to_statuses_to_tags ):
        
        self._service_keys_to_statuses_to_tags = service_keys_to_statuses_to_tags
        
        self._combined_namespaces_cache = None
        
    
    def _RecalcCombinedIfNeeded( self ):
        
        pass
        
    
    def Duplicate( self ):
        
        dupe_service_keys_to_statuses_to_tags = collections.defaultdict( HydrusData.default_dict_set )
        
        for ( service_key, statuses_to_tags ) in self._service_keys_to_statuses_to_tags.items():
            
            dupe_statuses_to_tags = HydrusData.default_dict_set()
            
            for ( status, tags ) in statuses_to_tags.items():
                
                dupe_statuses_to_tags[ status ] = set( tags )
                
            
            dupe_service_keys_to_statuses_to_tags[ service_key ] = dupe_statuses_to_tags
            
        
        return TagsManagerSimple( dupe_service_keys_to_statuses_to_tags )
        
    
    def GetCombinedNamespaces( self, namespaces ):
        
        self._RecalcCombinedIfNeeded()
        
        if self._combined_namespaces_cache is None:
    
            combined_statuses_to_tags = self._service_keys_to_statuses_to_tags[ CC.COMBINED_TAG_SERVICE_KEY ]
            
            combined_current = combined_statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ]
            combined_pending = combined_statuses_to_tags[ HC.CONTENT_STATUS_PENDING ]
            
            pairs = ( HydrusTags.SplitTag( tag ) for tag in combined_current.union( combined_pending ) )
            
            self._combined_namespaces_cache = HydrusData.BuildKeyToSetDict( ( namespace, subtag ) for ( namespace, subtag ) in pairs if namespace != '' )
            
        
        result = { namespace : self._combined_namespaces_cache[ namespace ] for namespace in namespaces }
        
        return result
        
    
    def GetComparableNamespaceSlice( self, namespaces ):
        
        self._RecalcCombinedIfNeeded()
        
        combined_statuses_to_tags = self._service_keys_to_statuses_to_tags[ CC.COMBINED_TAG_SERVICE_KEY ]
        
        combined_current = combined_statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ]
        combined_pending = combined_statuses_to_tags[ HC.CONTENT_STATUS_PENDING ]
        
        combined = combined_current.union( combined_pending )
        
        pairs = [ HydrusTags.SplitTag( tag ) for tag in combined ]
        
        slice = []
        
        for desired_namespace in namespaces:
            
            subtags = [ HydrusTags.ConvertTagToSortable( subtag ) for ( namespace, subtag ) in pairs if namespace == desired_namespace ]
            
            subtags.sort()
            
            slice.append( tuple( subtags ) )
            
        
        return tuple( slice )
        
    
    def GetCurrent( self, service_key = CC.COMBINED_TAG_SERVICE_KEY ):
        
        if service_key == CC.COMBINED_TAG_SERVICE_KEY:
            
            self._RecalcCombinedIfNeeded()
            
        
        statuses_to_tags = self._service_keys_to_statuses_to_tags[ service_key ]
        
        return set( statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ] )
        
    
    def GetDeleted( self, service_key = CC.COMBINED_TAG_SERVICE_KEY ):
        
        if service_key == CC.COMBINED_TAG_SERVICE_KEY:
            
            self._RecalcCombinedIfNeeded()
            
        
        statuses_to_tags = self._service_keys_to_statuses_to_tags[ service_key ]
        
        return set( statuses_to_tags[ HC.CONTENT_STATUS_DELETED ] )
        
    
    def GetNamespaceSlice( self, namespaces ):
        
        self._RecalcCombinedIfNeeded()
        
        combined_statuses_to_tags = self._service_keys_to_statuses_to_tags[ CC.COMBINED_TAG_SERVICE_KEY ]
        
        combined_current = combined_statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ]
        combined_pending = combined_statuses_to_tags[ HC.CONTENT_STATUS_PENDING ]
        
        combined = combined_current.union( combined_pending )
        
        slice = { tag for tag in combined if True in ( tag.startswith( namespace + ':' ) for namespace in namespaces ) }
        
        slice = frozenset( slice )
        
        return slice
        
    
    def GetPending( self, service_key = CC.COMBINED_TAG_SERVICE_KEY ):
        
        if service_key == CC.COMBINED_TAG_SERVICE_KEY:
            
            self._RecalcCombinedIfNeeded()
            
        
        statuses_to_tags = self._service_keys_to_statuses_to_tags[ service_key ]
        
        return set( statuses_to_tags[ HC.CONTENT_STATUS_PENDING ] )
        
    
    def GetPetitioned( self, service_key = CC.COMBINED_TAG_SERVICE_KEY ):
        
        if service_key == CC.COMBINED_TAG_SERVICE_KEY:
            
            self._RecalcCombinedIfNeeded()
            
        
        statuses_to_tags = self._service_keys_to_statuses_to_tags[ service_key ]
        
        return set( statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ] )
        
    
class TagsManager( TagsManagerSimple ):
    
    def __init__( self, service_keys_to_statuses_to_tags ):
        
        TagsManagerSimple.__init__( self, service_keys_to_statuses_to_tags )
        
        self._combined_is_calculated = False
        
        HG.client_controller.sub( self, 'NewSiblings', 'notify_new_siblings_data' )
        
    
    def _RecalcCombinedIfNeeded( self ):
        
        if not self._combined_is_calculated:
            
            # Combined tags are pre-collapsed by siblings
            
            siblings_manager = HG.client_controller.GetManager( 'tag_siblings' )
            
            combined_statuses_to_tags = collections.defaultdict( set )
            
            for ( service_key, statuses_to_tags ) in self._service_keys_to_statuses_to_tags.items():
                
                if service_key == CC.COMBINED_TAG_SERVICE_KEY:
                    
                    continue
                    
                
                statuses_to_tags = siblings_manager.CollapseStatusesToTags( service_key, statuses_to_tags )
                
                combined_statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ].update( statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ] )
                combined_statuses_to_tags[ HC.CONTENT_STATUS_PENDING ].update( statuses_to_tags[ HC.CONTENT_STATUS_PENDING ] )
                combined_statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ].update( statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ] )
                combined_statuses_to_tags[ HC.CONTENT_STATUS_DELETED ].update( statuses_to_tags[ HC.CONTENT_STATUS_DELETED ] )
                
            
            self._service_keys_to_statuses_to_tags[ CC.COMBINED_TAG_SERVICE_KEY ] = combined_statuses_to_tags
            
            self._combined_namespaces_cache = None
            
            self._combined_is_calculated = True
            
        
    
    def DeletePending( self, service_key ):
        
        statuses_to_tags = self._service_keys_to_statuses_to_tags[ service_key ]
        
        if len( statuses_to_tags[ HC.CONTENT_STATUS_PENDING ] ) + len( statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ] ) > 0:
            
            statuses_to_tags[ HC.CONTENT_STATUS_PENDING ] = set()
            statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ] = set()
            
            self._combined_is_calculated = False
            
        
    
    def Duplicate( self ):
        
        dupe_service_keys_to_statuses_to_tags = collections.defaultdict( HydrusData.default_dict_set )
        
        for ( service_key, statuses_to_tags ) in self._service_keys_to_statuses_to_tags.items():
            
            dupe_statuses_to_tags = HydrusData.default_dict_set()
            
            for ( status, tags ) in statuses_to_tags.items():
                
                dupe_statuses_to_tags[ status ] = set( tags )
                
            
            dupe_service_keys_to_statuses_to_tags[ service_key ] = dupe_statuses_to_tags
            
        
        return TagsManager( dupe_service_keys_to_statuses_to_tags )
        
    
    def GetNumTags( self, service_key, include_current_tags = True, include_pending_tags = False ):
        
        if service_key == CC.COMBINED_TAG_SERVICE_KEY:
            
            self._RecalcCombinedIfNeeded()
            
        
        num_tags = 0
        
        statuses_to_tags = self.GetStatusesToTags( service_key )
        
        if include_current_tags: num_tags += len( statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ] )
        if include_pending_tags: num_tags += len( statuses_to_tags[ HC.CONTENT_STATUS_PENDING ] )
        
        return num_tags
        
    
    def GetServiceKeysToStatusesToTags( self ):
        
        self._RecalcCombinedIfNeeded()
        
        return self._service_keys_to_statuses_to_tags
        
    
    def GetStatusesToTags( self, service_key ):
        
        if service_key == CC.COMBINED_TAG_SERVICE_KEY:
            
            self._RecalcCombinedIfNeeded()
            
        
        return self._service_keys_to_statuses_to_tags[ service_key ]
        
    
    def HasTag( self, tag ):
        
        self._RecalcCombinedIfNeeded()
        
        combined_statuses_to_tags = self._service_keys_to_statuses_to_tags[ CC.COMBINED_TAG_SERVICE_KEY ]
        
        return tag in combined_statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ] or tag in combined_statuses_to_tags[ HC.CONTENT_STATUS_PENDING ]
        
    
    def NewSiblings( self ):
        
        self._combined_is_calculated = False
        
    
    def ProcessContentUpdate( self, service_key, content_update ):
        
        statuses_to_tags = self._service_keys_to_statuses_to_tags[ service_key ]
        
        ( data_type, action, row ) = content_update.ToTuple()
        
        if action == HC.CONTENT_UPDATE_PETITION:
            
            ( tag, hashes, reason ) = row
            
        else:
            
            ( tag, hashes ) = row
            
        
        if action == HC.CONTENT_UPDATE_ADD:
            
            statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ].add( tag )
            
            statuses_to_tags[ HC.CONTENT_STATUS_DELETED ].discard( tag )
            statuses_to_tags[ HC.CONTENT_STATUS_PENDING ].discard( tag )
            
        elif action == HC.CONTENT_UPDATE_DELETE:
            
            statuses_to_tags[ HC.CONTENT_STATUS_DELETED ].add( tag )
            
            statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ].discard( tag )
            statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ].discard( tag )
            
        elif action == HC.CONTENT_UPDATE_PEND:
            
            if tag not in statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ]:
                
                statuses_to_tags[ HC.CONTENT_STATUS_PENDING ].add( tag )
                
            
        elif action == HC.CONTENT_UPDATE_RESCIND_PEND:
            
            statuses_to_tags[ HC.CONTENT_STATUS_PENDING ].discard( tag )
            
        elif action == HC.CONTENT_UPDATE_PETITION:
            
            if tag in statuses_to_tags[ HC.CONTENT_STATUS_CURRENT ]:
                
                statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ].add( tag )
                
            
        elif action == HC.CONTENT_UPDATE_RESCIND_PETITION: statuses_to_tags[ HC.CONTENT_STATUS_PETITIONED ].discard( tag )
        
        self._combined_is_calculated = False
        
    
    def ResetService( self, service_key ):
        
        if service_key in self._service_keys_to_statuses_to_tags:
            
            del self._service_keys_to_statuses_to_tags[ service_key ]
            
            self._combined_is_calculated = False
            
        
    
