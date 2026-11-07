[app]

title = Neon Strike
package.name = neonstrike
package.domain = org.game.neonstrike

source.dir = .
source.include_exts = py,txt

version = 1.0

requirements = python3,pygame

orientation = landscape
fullscreen = 1

android.permissions = VIBRATE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.accept_sdk_license = True

p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
