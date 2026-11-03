import Foundation

/// A JSON value. A number keeps its source text, so each field parses it once as the type it needs.
/// An object keeps its key order, because a journal line writes `seq` and `kind` first.
enum JSON: Sendable {
    case null
    case bool(Bool)
    case number(String)
    case string(String)
    case array([JSON])
    case object([(key: String, value: JSON)])

    static func int(_ value: Int) -> JSON { .number(String(value)) }

    /// The shortest text that parses back to the same bits, or nil for a value that JSON cannot hold.
    static func double(_ value: Double) -> JSON? {
        value.isFinite ? .number(value.description) : nil
    }
}

struct JSONSyntaxError: Error, Equatable {
    let offset: Int
}

// MARK: - Parse

extension JSON {
    static func parse(_ bytes: UnsafeBufferPointer<UInt8>) throws(JSONSyntaxError) -> JSON {
        var parser = Parser(bytes: bytes)
        parser.skipSpace()
        let value = try parser.value()
        parser.skipSpace()
        guard parser.i == bytes.count else { throw JSONSyntaxError(offset: parser.i) }
        return value
    }

    static func parse(_ data: Data) throws(JSONSyntaxError) -> JSON {
        let result: Result<JSON, JSONSyntaxError> = data.withUnsafeBytes { raw in
            Result { () throws(JSONSyntaxError) in try parse(raw.bindMemory(to: UInt8.self)) }
        }
        return try result.get()
    }
}

private struct Parser {
    let bytes: UnsafeBufferPointer<UInt8>
    var i = 0
    var depth = 0

    static let maxDepth = 64

    func fail() -> JSONSyntaxError { JSONSyntaxError(offset: i) }

    var current: UInt8? { i < bytes.count ? bytes[i] : nil }

    mutating func skipSpace() {
        while let c = current, c == 0x20 || c == 0x09 || c == 0x0A || c == 0x0D { i += 1 }
    }

    mutating func expect(_ literal: StaticString) throws(JSONSyntaxError) {
        let count = literal.utf8CodeUnitCount
        guard i + count <= bytes.count else { throw fail() }
        for k in 0..<count where bytes[i + k] != literal.utf8Start[k] { throw fail() }
        i += count
    }

    mutating func value() throws(JSONSyntaxError) -> JSON {
        switch current {
        case UInt8(ascii: "{"): return try object()
        case UInt8(ascii: "["): return try array()
        case UInt8(ascii: "\""): return .string(try string())
        case UInt8(ascii: "t"): try expect("true"); return .bool(true)
        case UInt8(ascii: "f"): try expect("false"); return .bool(false)
        case UInt8(ascii: "n"): try expect("null"); return .null
        case .some(let c) where c == UInt8(ascii: "-") || (0x30...0x39).contains(c): return .number(try number())
        default: throw fail()
        }
    }

    mutating func nest() throws(JSONSyntaxError) {
        depth += 1
        guard depth <= Self.maxDepth else { throw fail() }
        i += 1
        skipSpace()
    }

    mutating func object() throws(JSONSyntaxError) -> JSON {
        try nest()
        var pairs: [(key: String, value: JSON)] = []
        if current == UInt8(ascii: "}") {
            i += 1
            depth -= 1
            return .object(pairs)
        }
        while true {
            guard current == UInt8(ascii: "\"") else { throw fail() }
            let key = try string()
            skipSpace()
            guard current == UInt8(ascii: ":") else { throw fail() }
            i += 1
            skipSpace()
            pairs.append((key, try value()))
            skipSpace()
            switch current {
            case UInt8(ascii: ","): i += 1; skipSpace()
            case UInt8(ascii: "}"): i += 1; depth -= 1; return .object(pairs)
            default: throw fail()
            }
        }
    }

    mutating func array() throws(JSONSyntaxError) -> JSON {
        try nest()
        var items: [JSON] = []
        if current == UInt8(ascii: "]") {
            i += 1
            depth -= 1
            return .array(items)
        }
        while true {
            items.append(try value())
            skipSpace()
            switch current {
            case UInt8(ascii: ","): i += 1; skipSpace()
            case UInt8(ascii: "]"): i += 1; depth -= 1; return .array(items)
            default: throw fail()
            }
        }
    }

    mutating func digits() throws(JSONSyntaxError) {
        guard let c = current, (0x30...0x39).contains(c) else { throw fail() }
        while let c = current, (0x30...0x39).contains(c) { i += 1 }
    }

    mutating func number() throws(JSONSyntaxError) -> String {
        let start = i
        if current == UInt8(ascii: "-") { i += 1 }
        if current == UInt8(ascii: "0") {
            i += 1
        } else {
            try digits()
        }
        if current == UInt8(ascii: ".") {
            i += 1
            try digits()
        }
        if current == UInt8(ascii: "e") || current == UInt8(ascii: "E") {
            i += 1
            if current == UInt8(ascii: "+") || current == UInt8(ascii: "-") { i += 1 }
            try digits()
        }
        return String(decoding: UnsafeBufferPointer(rebasing: bytes[start..<i]), as: UTF8.self)
    }

    mutating func string() throws(JSONSyntaxError) -> String {
        i += 1
        let start = i
        while let c = current, c != UInt8(ascii: "\""), c != UInt8(ascii: "\\"), c >= 0x20 { i += 1 }
        if current == UInt8(ascii: "\"") {
            let text = String(validating: UnsafeBufferPointer(rebasing: bytes[start..<i]), as: UTF8.self)
            guard let text else { throw JSONSyntaxError(offset: start) }
            i += 1
            return text
        }
        var utf8 = Array(UnsafeBufferPointer(rebasing: bytes[start..<i]))
        while true {
            guard let c = current, c >= 0x20 else { throw fail() }
            i += 1
            switch c {
            case UInt8(ascii: "\""):
                guard let text = String(validating: utf8, as: UTF8.self) else { throw JSONSyntaxError(offset: start) }
                return text
            case UInt8(ascii: "\\"):
                try escape(into: &utf8)
            default:
                utf8.append(c)
            }
        }
    }

    mutating func escape(into utf8: inout [UInt8]) throws(JSONSyntaxError) {
        guard let c = current else { throw fail() }
        i += 1
        switch c {
        case UInt8(ascii: "\""), UInt8(ascii: "\\"), UInt8(ascii: "/"): utf8.append(c)
        case UInt8(ascii: "b"): utf8.append(0x08)
        case UInt8(ascii: "f"): utf8.append(0x0C)
        case UInt8(ascii: "n"): utf8.append(0x0A)
        case UInt8(ascii: "r"): utf8.append(0x0D)
        case UInt8(ascii: "t"): utf8.append(0x09)
        case UInt8(ascii: "u"):
            var scalar = try hex4()
            if (0xD800...0xDBFF).contains(scalar) {
                try expect("\\u")
                let low = try hex4()
                guard (0xDC00...0xDFFF).contains(low) else { throw fail() }
                scalar = 0x10000 + ((scalar - 0xD800) << 10) + (low - 0xDC00)
            }
            guard let unicode = Unicode.Scalar(scalar) else { throw fail() }
            utf8.append(contentsOf: String(Character(unicode)).utf8)
        default:
            throw fail()
        }
    }

    mutating func hex4() throws(JSONSyntaxError) -> UInt32 {
        guard i + 4 <= bytes.count else { throw fail() }
        var value: UInt32 = 0
        for _ in 0..<4 {
            let c = bytes[i]
            let digit: UInt8
            switch c {
            case 0x30...0x39: digit = c - 0x30
            case 0x41...0x46: digit = c - 0x41 + 10
            case 0x61...0x66: digit = c - 0x61 + 10
            default: throw fail()
            }
            value = value << 4 | UInt32(digit)
            i += 1
        }
        return value
    }
}

// MARK: - Write

extension JSON {
    /// Compact text with the separators `,` and `:`, the same bytes as Python `json.dumps(separators=(",", ":"))`.
    func write(into out: inout [UInt8]) {
        switch self {
        case .null: out.append(contentsOf: "null".utf8)
        case .bool(let b): out.append(contentsOf: (b ? "true" : "false").utf8)
        case .number(let text): out.append(contentsOf: text.utf8)
        case .string(let s): Self.writeString(s, into: &out)
        case .array(let items):
            out.append(UInt8(ascii: "["))
            for (n, item) in items.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                item.write(into: &out)
            }
            out.append(UInt8(ascii: "]"))
        case .object(let pairs):
            out.append(UInt8(ascii: "{"))
            for (n, pair) in pairs.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                Self.writeString(pair.key, into: &out)
                out.append(UInt8(ascii: ":"))
                pair.value.write(into: &out)
            }
            out.append(UInt8(ascii: "}"))
        }
    }

    /// Text indented by one space for each level, the same bytes as Python `json.dumps(indent=1)`.
    func writePretty(into out: inout [UInt8], level: Int = 0) {
        func newline(_ level: Int) {
            out.append(0x0A)
            out.append(contentsOf: repeatElement(UInt8(ascii: " "), count: level))
        }
        switch self {
        case .array(let items) where !items.isEmpty:
            out.append(UInt8(ascii: "["))
            for (n, item) in items.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                newline(level + 1)
                item.writePretty(into: &out, level: level + 1)
            }
            newline(level)
            out.append(UInt8(ascii: "]"))
        case .object(let pairs) where !pairs.isEmpty:
            out.append(UInt8(ascii: "{"))
            for (n, pair) in pairs.enumerated() {
                if n > 0 { out.append(UInt8(ascii: ",")) }
                newline(level + 1)
                Self.writeString(pair.key, into: &out)
                out.append(contentsOf: ": ".utf8)
                pair.value.writePretty(into: &out, level: level + 1)
            }
            newline(level)
            out.append(UInt8(ascii: "}"))
        default:
            write(into: &out)
        }
    }

    /// Escapes like Python `json.dumps(ensure_ascii=False)`: quote, backslash, and control characters only.
    static func writeString(_ s: String, into out: inout [UInt8]) {
        out.append(UInt8(ascii: "\""))
        for byte in s.utf8 {
            switch byte {
            case UInt8(ascii: "\""): out.append(contentsOf: "\\\"".utf8)
            case UInt8(ascii: "\\"): out.append(contentsOf: "\\\\".utf8)
            case 0x0A: out.append(contentsOf: "\\n".utf8)
            case 0x0D: out.append(contentsOf: "\\r".utf8)
            case 0x09: out.append(contentsOf: "\\t".utf8)
            case 0x08: out.append(contentsOf: "\\b".utf8)
            case 0x0C: out.append(contentsOf: "\\f".utf8)
            case 0..<0x20:
                let hex = Array("0123456789abcdef".utf8)
                out.append(contentsOf: "\\u00".utf8)
                out.append(hex[Int(byte >> 4)])
                out.append(hex[Int(byte & 0xF)])
            default: out.append(byte)
            }
        }
        out.append(UInt8(ascii: "\""))
    }

    func prettyData() -> Data {
        var out: [UInt8] = []
        writePretty(into: &out)
        out.append(0x0A)
        return Data(out)
    }
}
