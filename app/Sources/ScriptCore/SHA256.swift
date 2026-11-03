import Foundation

/// SHA-256 (FIPS 180-4). ScriptCore uses Foundation only, and Foundation has no hash function.
struct SHA256 {
    private var state: [UInt32] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]
    private var pending: [UInt8] = []
    private var length: UInt64 = 0

    private static let k: [UInt32] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ]

    static func hex(_ data: Data) -> String {
        var hash = SHA256()
        hash.update(data)
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    mutating func update(_ data: Data) {
        data.withUnsafeBytes { raw in update(raw.bindMemory(to: UInt8.self)) }
    }

    mutating func update(_ bytes: UnsafeBufferPointer<UInt8>) {
        length &+= UInt64(bytes.count)
        var offset = 0
        if !pending.isEmpty {
            let take = min(64 - pending.count, bytes.count)
            pending.append(contentsOf: UnsafeBufferPointer(rebasing: bytes[0..<take]))
            offset = take
            guard pending.count == 64 else { return }
            pending.withUnsafeBufferPointer { compress($0) }
            pending.removeAll(keepingCapacity: true)
        }
        while offset + 64 <= bytes.count {
            compress(UnsafeBufferPointer(rebasing: bytes[offset..<offset + 64]))
            offset += 64
        }
        pending.append(contentsOf: UnsafeBufferPointer(rebasing: bytes[offset...]))
    }

    mutating func finalize() -> [UInt8] {
        let bits = length &* 8
        var tail: [UInt8] = [0x80]
        tail.append(contentsOf: repeatElement(0, count: (119 - pending.count) % 64))
        tail.append(contentsOf: (0..<8).reversed().map { UInt8(truncatingIfNeeded: bits >> ($0 * 8)) })
        let saved = length
        tail.withUnsafeBufferPointer { update($0) }
        length = saved
        return state.flatMap { word in (0..<4).reversed().map { UInt8(truncatingIfNeeded: word >> ($0 * 8)) } }
    }

    private mutating func compress(_ block: UnsafeBufferPointer<UInt8>) {
        withUnsafeTemporaryAllocation(of: UInt32.self, capacity: 64) { w in
            for t in 0..<16 {
                w[t] = UInt32(block[4 * t]) << 24 | UInt32(block[4 * t + 1]) << 16
                    | UInt32(block[4 * t + 2]) << 8 | UInt32(block[4 * t + 3])
            }
            for t in 16..<64 {
                let s0 = w[t - 15].rotatedRight(7) ^ w[t - 15].rotatedRight(18) ^ (w[t - 15] >> 3)
                let s1 = w[t - 2].rotatedRight(17) ^ w[t - 2].rotatedRight(19) ^ (w[t - 2] >> 10)
                w[t] = w[t - 16] &+ s0 &+ w[t - 7] &+ s1
            }
            var (a, b, c, d) = (state[0], state[1], state[2], state[3])
            var (e, f, g, h) = (state[4], state[5], state[6], state[7])
            for t in 0..<64 {
                let s1 = e.rotatedRight(6) ^ e.rotatedRight(11) ^ e.rotatedRight(25)
                let choose = (e & f) ^ (~e & g)
                let t1 = h &+ s1 &+ choose &+ Self.k[t] &+ w[t]
                let s0 = a.rotatedRight(2) ^ a.rotatedRight(13) ^ a.rotatedRight(22)
                let majority = (a & b) ^ (a & c) ^ (b & c)
                (h, g, f, e, d, c, b, a) = (g, f, e, d &+ t1, c, b, a, t1 &+ s0 &+ majority)
            }
            state[0] &+= a
            state[1] &+= b
            state[2] &+= c
            state[3] &+= d
            state[4] &+= e
            state[5] &+= f
            state[6] &+= g
            state[7] &+= h
        }
    }
}

private extension UInt32 {
    func rotatedRight(_ n: UInt32) -> UInt32 { (self >> n) | (self << (32 - n)) }
}
