CC ?= cc
OUT ?= ../build/host
CFLAGS ?= -Os -g -std=c11 -Wall -Wextra -Werror -pthread
CPPFLAGS += -Isrc -Ivendor/civetweb/include -Ivendor/jsmn -Ivendor/sqlite -I$(OUT) -D_DEFAULT_SOURCE -DNO_SSL -DNO_CGI -DNO_FILES -DNO_CACHING -DUSE_WEBSOCKET -DMG_MAX_UNANSWERED_PING=2
CPPFLAGS += -DDISC_HTTP_CLIENT_TIMEOUT_MS=3000
# The source commit (scripts/build.sh passes it), shown by /api/about.
DISC_BUILD ?= unknown
CPPFLAGS += -DDISC_BUILD='"$(DISC_BUILD)"'
# A new build ID rebuilds main.o even when no source changed, so /api/about
# never shows a stale one. Checked while the makefile is read, before make
# looks at any file (it may compare times only to the second).
ifneq ($(DISC_BUILD),$(shell cat $(OUT)/build-id 2>/dev/null))
$(shell mkdir -p $(OUT) && rm -f $(OUT)/main.o && printf '%s\n' '$(DISC_BUILD)' > $(OUT)/build-id)
endif
SQLITE_FLAGS = -DSQLITE_THREADSAFE=2 -DSQLITE_OMIT_LOAD_EXTENSION -DSQLITE_OMIT_DEPRECATED -DSQLITE_OMIT_SHARED_CACHE -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_MAX_MMAP_SIZE=0 -DSQLITE_DEFAULT_CACHE_SIZE=-64 -DSQLITE_DQS=0 -DSQLITE_TEMP_STORE=3 -DSQLITE_LIKE_DOESNT_MATCH_BLOBS -DSQLITE_OMIT_PROGRESS_CALLBACK
LDLIBS += -pthread -lm
ifeq ($(shell uname -s),Linux)
LDLIBS += -ldl -lrt
endif
.PHONY: all test
all: $(OUT)/disc-service
$(OUT):
	mkdir -p $@
$(OUT)/main.o: src/main.c src/settings.h $(OUT)/manager_assets.h src/framing.h src/webroot.h src/catalog.h src/data.h src/media.h src/facts.h src/history.h src/database.h src/store.h src/trash.h src/lists.h src/tree.h src/origins.h src/log.h src/jsonutil.h vendor/civetweb/include/civetweb.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/catalog.o: src/catalog.c src/catalog.h src/jsonutil.h vendor/jsmn/jsmn.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/jsonutil.o: src/jsonutil.c src/jsonutil.h src/framing.h vendor/jsmn/jsmn.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/data.o: src/data.c src/data.h src/database.h src/jsonutil.h src/framing.h vendor/sqlite/sqlite3.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/sqlite3.o: vendor/sqlite/sqlite3.c vendor/sqlite/sqlite3.h | $(OUT)
	$(CC) $(CPPFLAGS) $(SQLITE_FLAGS) $(filter-out -Werror -Wextra -Wall,$(CFLAGS)) -w -c $< -o $@
$(OUT)/webroot.o: src/webroot.c src/webroot.h src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/media.o: src/media.c src/media.h src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/facts.o: src/facts.c src/facts.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/history.o: src/history.c src/history.h src/database.h src/framing.h src/jsonutil.h src/media.h vendor/sqlite/sqlite3.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/database.o: src/database.c src/database.h src/log.h vendor/sqlite/sqlite3.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/log.o: src/log.c src/log.h src/jsonutil.h src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/origins.o: src/origins.c src/origins.h src/jsonutil.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/trash.o: src/trash.c src/trash.h src/database.h src/history.h src/jsonutil.h src/framing.h vendor/sqlite/sqlite3.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/tree.o: src/tree.c src/tree.h src/media.h src/jsonutil.h src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/lists.o: src/lists.c src/lists.h src/media.h src/jsonutil.h src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/store.o: src/store.c src/store.h src/database.h src/jsonutil.h src/framing.h vendor/sqlite/sqlite3.h vendor/civetweb/include/civetweb.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/settings.o: src/settings.c src/settings.h src/webroot.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/framing.o: src/framing.c src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/civetweb.o: vendor/civetweb/src/civetweb.c $(wildcard vendor/civetweb/src/*.inl) vendor/civetweb/include/civetweb.h | $(OUT)
	$(CC) $(CPPFLAGS) $(filter-out -Werror,$(CFLAGS)) -c $< -o $@
$(OUT)/disc-service: $(OUT)/main.o $(OUT)/settings.o $(OUT)/framing.o $(OUT)/webroot.o $(OUT)/media.o $(OUT)/facts.o $(OUT)/history.o $(OUT)/database.o $(OUT)/store.o $(OUT)/trash.o $(OUT)/lists.o $(OUT)/tree.o $(OUT)/origins.o $(OUT)/log.o $(OUT)/catalog.o $(OUT)/jsonutil.o $(OUT)/data.o $(OUT)/sqlite3.o $(OUT)/civetweb.o
	$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@
$(OUT)/framing-test: tests/framing_test.c src/framing.c src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/framing_test.c src/framing.c $(LDFLAGS) -o $@
test: $(OUT)/framing-test
	$(OUT)/framing-test
