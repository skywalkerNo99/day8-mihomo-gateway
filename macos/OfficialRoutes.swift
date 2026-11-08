import AppKit
import ApplicationServices

struct VendorRoute: Identifiable {
    let id: String
    let name: String
    let latency: Int?
}

enum VendorPanel {
    static let bundle = "com.niceax.d8-desktop"

    enum Failure: String, Error {
        case permission = "请在系统设置 → 隐私与安全性 → 辅助功能中允许 Day8 Gateway，然后再次刷新。"
        case panel = "请在官方 Day8 主窗口点当前线路，打开「服务器」面板后再刷新。"
        case absent = "请先打开官方 Day8，并登录账号。"
    }

    static func value(_ element: AXUIElement, _ attribute: CFString) -> AnyObject? {
        var result: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute, &result) == .success else { return nil }
        return result
    }

    static func read() throws -> [VendorRoute] {
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first else { throw Failure.absent }
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(options) else { throw Failure.permission }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 1)
        var queue = value(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
        var found = [VendorRoute]()
        var visited = Set<AXUIElement>()
        let pattern = try NSRegularExpression(pattern: #"^([A-Z0-9_-]{6,64})\s+(.+?)\s+[0-9]+(?:\.[0-9]+)?(?:\s+([0-9]+)\s*ms|\s+.*)?$"#)
        while let item = queue.popLast(), visited.count < 3000 {
            guard visited.insert(item).inserted else { continue }
            if value(item, kAXRoleAttribute as CFString) as? String == "AXButton" {
                for attr in [kAXTitleAttribute, kAXDescriptionAttribute] {
                    guard let text = value(item, attr as CFString) as? String else { continue }
                    let source = text as NSString
                    guard let m = pattern.firstMatch(in: text, range: NSRange(location: 0, length: source.length)) else { continue }
                    let ms = m.range(at: 3)
                    found.append(VendorRoute(id: source.substring(with: m.range(at: 1)),
                                             name: source.substring(with: m.range(at: 2)),
                                             latency: ms.location == NSNotFound ? nil : Int(source.substring(with: ms))))
                    break
                }
            }
            if let children = value(item, kAXChildrenAttribute as CFString) as? [AXUIElement] {
                queue.append(contentsOf: children.reversed())
            }
        }
        guard !found.isEmpty, Set(found.map(\.id)).count == found.count else { throw Failure.panel }
        return found
    }
}
