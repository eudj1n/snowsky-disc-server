CC ?= cc
OUT ?= ../build/host
CFLAGS ?= -Os -g -std=c11 -Wall -Wextra -Werror -pthread
CPPFLAGS += -Isrc -Ivendor/civetweb/include -Ivendor/jsmn -Ivendor/sqlite -I$(OUT) -D_DEFAULT_SOURCE -DNO_SSL -DNO_CGI -DNO_FILES -DNO_CACHING -DUSE_WEBSOCKET -DMG_MAX_UNANSWERED_PING=2
CPPFLAGS += -DDISC_HTTP_CLIENT_TIMEOUT_MS=3000
# The source commit (scripts/build.sh passes it), shown by /api/about.
DISC_BUILD ?= unknown
CPPFLAGS += -DDISC_BUILD='"$(DISC_BUILD)"'
# civetweb's system information carries a build string, __DATE__ unless BUILD_DATE is set: the
# build id instead, so that a release built later from the same sources has the same bytes
# (2026-10-07: 2.57.3's package held the date its civetweb object was compiled).
CPPFLAGS += -DBUILD_DATE='"$(DISC_BUILD)"'
# A new build ID rebuilds the objects that carry it (main.o, and civetweb.o through BUILD_DATE) even
# when no source changed, so /api/about never shows a stale one and a release is the bytes a clean
# build gives (2026-10-10: 2.57.6's package held civetweb's object of an earlier build). Checked
# while the makefile is read, before make looks at any file (it may compare times only to the second).
ifneq ($(DISC_BUILD),$(shell cat $(OUT)/build-id 2>/dev/null))
$(shell mkdir -p $(OUT) && rm -f $(OUT)/main.o $(OUT)/civetweb.o && printf '%s\n' '$(DISC_BUILD)' > $(OUT)/build-id)
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
$(OUT)/main.o: src/main.c src/settings.h src/apps.h src/update.h $(OUT)/manager_assets.h src/framing.h src/webroot.h src/catalog.h src/data.h src/media.h src/facts.h src/history.h src/database.h src/store.h src/trash.h src/lists.h src/tree.h src/origins.h src/log.h src/jsonutil.h vendor/civetweb/include/civetweb.h | $(OUT)
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
$(OUT)/update.o: src/update.c src/update.h src/sha256.h src/jsonutil.h vendor/monocypher/monocypher-ed25519.h | $(OUT)
	$(CC) $(CPPFLAGS) -Ivendor/monocypher $(CFLAGS) -c $< -o $@
$(OUT)/sha256.o: src/sha256.c src/sha256.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/monocypher.o: vendor/monocypher/monocypher.c vendor/monocypher/monocypher.h | $(OUT)
	$(CC) $(CPPFLAGS) -Ivendor/monocypher $(CFLAGS) -c $< -o $@
$(OUT)/monocypher-ed25519.o: vendor/monocypher/monocypher-ed25519.c vendor/monocypher/monocypher-ed25519.h vendor/monocypher/monocypher.h | $(OUT)
	$(CC) $(CPPFLAGS) -Ivendor/monocypher $(CFLAGS) -c $< -o $@
$(OUT)/apps.o: src/apps.c src/apps.h src/webroot.h vendor/miniz/miniz.h | $(OUT)
	$(CC) $(CPPFLAGS) -Ivendor/miniz $(CFLAGS) -c $< -o $@
$(OUT)/miniz.o: vendor/miniz/miniz.c vendor/miniz/miniz.h | $(OUT)
	$(CC) $(CPPFLAGS) -DMINIZ_NO_STDIO -DMINIZ_NO_TIME -DMINIZ_NO_ARCHIVE_APIS -DMINIZ_NO_ARCHIVE_WRITING_APIS -DMINIZ_NO_ZLIB_APIS -DMINIZ_NO_ZLIB_COMPATIBLE_NAMES -DMINIZ_NO_DEFLATE_APIS $(filter-out -Werror -Wextra -Wall,$(CFLAGS)) -w -c $< -o $@
$(OUT)/settings.o: src/settings.c src/settings.h src/webroot.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/framing.o: src/framing.c src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
$(OUT)/civetweb.o: vendor/civetweb/src/civetweb.c $(wildcard vendor/civetweb/src/*.inl) vendor/civetweb/include/civetweb.h | $(OUT)
	$(CC) $(CPPFLAGS) $(filter-out -Werror,$(CFLAGS)) -c $< -o $@
$(OUT)/disc-service: $(OUT)/main.o $(OUT)/settings.o $(OUT)/apps.o $(OUT)/miniz.o $(OUT)/update.o $(OUT)/sha256.o $(OUT)/monocypher.o $(OUT)/monocypher-ed25519.o $(OUT)/framing.o $(OUT)/webroot.o $(OUT)/media.o $(OUT)/facts.o $(OUT)/history.o $(OUT)/database.o $(OUT)/store.o $(OUT)/trash.o $(OUT)/lists.o $(OUT)/tree.o $(OUT)/origins.o $(OUT)/log.o $(OUT)/catalog.o $(OUT)/jsonutil.o $(OUT)/data.o $(OUT)/sqlite3.o $(OUT)/civetweb.o
	$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@
$(OUT)/framing-test: tests/framing_test.c src/framing.c src/framing.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/framing_test.c src/framing.c $(LDFLAGS) -o $@
$(OUT)/app-install-tool: tests/app_install_tool.c $(OUT)/apps.o $(OUT)/miniz.o $(OUT)/webroot.o $(OUT)/framing.o | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/app_install_tool.c $(OUT)/apps.o $(OUT)/miniz.o $(OUT)/webroot.o $(OUT)/framing.o $(LDFLAGS) $(LDLIBS) -o $@
$(OUT)/sha256-test: tests/sha256_test.c src/sha256.c src/sha256.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/sha256_test.c src/sha256.c $(LDFLAGS) -o $@
$(OUT)/update-tool: tests/update_tool.c $(OUT)/update.o $(OUT)/sha256.o $(OUT)/monocypher.o $(OUT)/monocypher-ed25519.o $(OUT)/jsonutil.o $(OUT)/framing.o | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/update_tool.c $(OUT)/update.o $(OUT)/sha256.o $(OUT)/monocypher.o $(OUT)/monocypher-ed25519.o $(OUT)/jsonutil.o $(OUT)/framing.o $(LDFLAGS) $(LDLIBS) -o $@
test: $(OUT)/framing-test $(OUT)/app-install-tool $(OUT)/sha256-test $(OUT)/update-tool
	$(OUT)/framing-test
	$(OUT)/sha256-test
