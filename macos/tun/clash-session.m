// User-session operations only. Never installed or run as root.
#import <AppKit/AppKit.h>
#import <CoreServices/CoreServices.h>

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 2) return 2;
        NSString *op = [NSString stringWithUTF8String:argv[1]];
        NSString *bundle = @"com.metacubex.ClashX.meta";
        NSArray *apps = [NSRunningApplication runningApplicationsWithBundleIdentifier:bundle];
        NSURL *url = [NSWorkspace.sharedWorkspace URLForApplicationWithBundleIdentifier:bundle];
        if (!url) return 1;
        if ([op isEqualToString:@"quit"]) {
            for (NSRunningApplication *app in apps) if (![app terminate]) return 1;
            return 0;
        }
        if ([op isEqualToString:@"running"]) return apps.count ? 0 : 1;
        if ([op isEqualToString:@"open"]) return [NSWorkspace.sharedWorkspace openURL:url] ? 0 : 1;

        LSSharedFileListRef list = LSSharedFileListCreate(NULL, kLSSharedFileListSessionLoginItems, NULL);
        if (!list) return 2;
        NSArray *items = CFBridgingRelease(LSSharedFileListCopySnapshot(list, NULL));
        if (!items) return 2;
        BOOL found = NO;
        for (id object in items) {
            LSSharedFileListItemRef item = (__bridge LSSharedFileListItemRef)object;
            NSURL *saved = CFBridgingRelease(LSSharedFileListItemCopyResolvedURL(item, kLSSharedFileListNoUserInteraction | kLSSharedFileListDoNotMountVolumes, NULL));
            if (!saved || ![[NSBundle bundleWithURL:saved].bundleIdentifier isEqualToString:bundle]) continue;
            found = YES;
            if ([op isEqualToString:@"disable-login"] && LSSharedFileListItemRemove(list,item) != noErr) return 2;
        }
        if ([op isEqualToString:@"login"]) return found ? 0 : 1;
        if ([op isEqualToString:@"enable-login"] && !found) {
            LSSharedFileListItemRef added = LSSharedFileListInsertItemURL(list, kLSSharedFileListItemLast, NULL, NULL, (__bridge CFURLRef)url, NULL, NULL);
            if (!added) return 2;
            CFRelease(added);
        }
        CFRelease(list);
        return 0;
    }
}
