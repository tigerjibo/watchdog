#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# Copyright 2012 Google, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
:module: watchdog.observers.fsevents
:synopsis: FSEvents based emitter implementation.
:author: yesudeep@google.com (Yesudeep Mangalapilly)
:platforms: Mac OS X
"""

from __future__ import with_statement
from watchdog.utils import platform

if platform.is_darwin():
  import threading
  import unicodedata
  import os.path
  import _watchdog_fsevents as _fsevents

  from pathtools.path import absolute_path
  from watchdog.events import\
    FileDeletedEvent,\
    FileModifiedEvent,\
    FileCreatedEvent,\
    FileMovedEvent,\
    DirDeletedEvent,\
    DirModifiedEvent,\
    DirCreatedEvent,\
    DirMovedEvent,\
    DirMovedEvent
  from watchdog.utils.dirsnapshot import DirectorySnapshot
  from watchdog.observers.api import\
    BaseObserver,\
    EventEmitter,\
    DEFAULT_EMITTER_TIMEOUT,\
    DEFAULT_OBSERVER_TIMEOUT

  class FSEventsStreamFlag:
    MustScanSubDirs = 0x00000001
    UserDropped = 0x00000002
    KernelDropped = 0x00000004

  class FSEventsEmitter(EventEmitter):
    """
    Mac OS X FSEvents Emitter class.

    :param event_queue:
        The event queue to fill with events.
    :param watch:
        A watch object representing the directory to monitor.
    :type watch:
        :class:`watchdog.observers.api.ObservedWatch`
    :param timeout:
        Read events blocking timeout (in seconds).
    :type timeout:
        ``float``
    """

    def __init__(self, event_queue, watch, timeout=DEFAULT_EMITTER_TIMEOUT):
      EventEmitter.__init__(self, event_queue, watch, timeout)
      self._lock = threading.Lock()
      self.snapshot = DirectorySnapshot(os.path.realpath(watch.path), watch.is_recursive)

    def on_thread_exit(self):
      _fsevents.remove_watch(self.watch)
      _fsevents.stop(self)

    def queue_events(self, timeout):
      for idx in xrange(len(self.pathnames)):
        event_path = absolute_path(self.pathnames[idx])
        event_flags = self.flags[idx]

        if not self.watch.is_recursive and self.watch.path != event_path:
          return

        recursive_update = bool(event_flags & FSEventsStreamFlag.MustScanSubDirs)

        # try to build only partial snapshot
        new_snapshot = DirectorySnapshot(
          event_path,
          recursive_update
        )

        if recursive_update and self.watch.path == event_path:
          # no optimization is possible
          events = new_snapshot - self.snapshot
          self.snapshot = new_snapshot
        else:
          # partial comparison will be done
          previous_snapshot = self.snapshot.copy(event_path, recursive_update)

          # compare them
          events = new_snapshot - previous_snapshot

          if events.dirs_deleted or events.dirs_created or events.dirs_moved:
            # add files from deleted dir to previous snapshot
            previous_snapshot.add_entries(self.snapshot.copy_multiple(events.dirs_deleted, True))

            # add files from created dir to new_snapshot, create a recursive snapshot of new dir
            for new_path in events.dirs_created:
              new_snapshot.add_entries(DirectorySnapshot(new_path, True))

            previous_snapshot.add_entries(
              self.snapshot.copy_multiple(
                [old_path for (old_path, new_path) in events.dirs_moved],
                True
              )
            )
            for old_path, new_path in events.dirs_moved:
              new_snapshot.add_entries(DirectorySnapshot(new_path, True))

            # re-do diff
            events = new_snapshot - previous_snapshot

          # update last snapshot
          self.snapshot.remove_entries(previous_snapshot)
          self.snapshot.add_entries(new_snapshot)

        # Files.
        for src_path in events.files_deleted:
          self.queue_event(FileDeletedEvent(src_path))
        for src_path in events.files_modified:
          self.queue_event(FileModifiedEvent(src_path))
        for src_path in events.files_created:
          self.queue_event(FileCreatedEvent(src_path))
        for src_path, dest_path in events.files_moved:
          self.queue_event(FileMovedEvent(src_path, dest_path))

        # Directories.
        for src_path in events.dirs_deleted:
          self.queue_event(DirDeletedEvent(src_path))
        for src_path in events.dirs_modified:
          self.queue_event(DirModifiedEvent(src_path))
        for src_path in events.dirs_created:
          self.queue_event(DirCreatedEvent(src_path))
        for src_path, dest_path in events.dirs_moved:
          self.queue_event(DirMovedEvent(src_path, dest_path))


    def run(self):
      try:
        def callback(pathnames, flags):
          with self._lock:
            self.pathnames = pathnames
            self.flags = flags
            self.queue_events(self.timeout)

        #for pathname, flag in zip(pathnames, flags):
        #if emitter.watch.is_recursive: # and pathname != emitter.watch.path:
        #    new_sub_snapshot = DirectorySnapshot(pathname, True)
        #    old_sub_snapshot = self.snapshot.copy(pathname)
        #    diff = new_sub_snapshot - old_sub_snapshot
        #    self.snapshot += new_subsnapshot
        #else:
        #    new_snapshot = DirectorySnapshot(emitter.watch.path, False)
        #    diff = new_snapshot - emitter.snapshot
        #    emitter.snapshot = new_snapshot


        # INFO: FSEvents reports directory notifications recursively
        # by default, so we do not need to add subdirectory paths.
        #pathnames = set([self.watch.path])
        #if self.watch.is_recursive:
        #    for root, directory_names, _ in os.walk(self.watch.path):
        #        for directory_name in directory_names:
        #            full_path = absolute_path(
        #                            os.path.join(root, directory_name))
        #            pathnames.add(full_path)

        self.pathnames = [self.watch.path]
        _fsevents.add_watch(self,
                            self.watch,
                            callback,
                            [self.watch.path])
        _fsevents.read_events(self)
      except Exception, e:
        pass
      finally:
        self.on_thread_exit()


  class FSEventsObserver(BaseObserver):
    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
      BaseObserver.__init__(self, emitter_class=FSEventsEmitter,
                            timeout=timeout)

    def schedule(self, event_handler, path, recursive=False):
      # Fix for issue #26: Trace/BPT error when given a unicode path
      # string. https://github.com/gorakhargosh/watchdog/issues#issue/26
      if isinstance(path, unicode):
        #path = unicode(path, 'utf-8')
        path = unicodedata.normalize('NFC', path).encode('utf-8')
      return BaseObserver.schedule(self, event_handler, path, recursive)
