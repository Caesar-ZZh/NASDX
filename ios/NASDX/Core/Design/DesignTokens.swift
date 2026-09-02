import SwiftUI

/// 设计系统：间距 / 圆角 / 字号 token + 语义色。
/// 配色整体源自 robinhood DESIGN-swiftui.md（design-md/finance/robinhood），并适配 A 股「红涨绿跌」。
/// ⚠️ robinhood 为「绿涨红跌」；A 股反转：涨 = 红(#E62232 真红)，跌 = 绿(#00A904 品牌绿)。
///    其品牌绿 #00C805 与品牌橙 #FF5000 在 A 股语义下请勿直接当涨跌色使用。
enum DesignTokens {
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
    }
    enum Radius {
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 20
    }
    enum FontSize {
        static let caption: CGFloat = 12
        static let body: CGFloat = 15
        static let title: CGFloat = 17
        static let largeTitle: CGFloat = 28
    }
}

extension Color {
    // MARK: - 画布 / 表面（robinhood 亮色套）
    static let canvas   = Color(red: 1.000, green: 1.000, blue: 1.000) // #FFFFFF
    static let surface1 = Color(red: 0.969, green: 0.969, blue: 0.969) // #F7F7F7
    static let surface2 = Color(red: 0.937, green: 0.937, blue: 0.937) // #EFEFEF
    static let divider  = Color(red: 0.902, green: 0.902, blue: 0.902) // #E6E6E6

    // MARK: - 文本（亮色）
    static let textPrimary   = Color(red: 0.000, green: 0.000, blue: 0.000) // #000000
    static let textSecondary = Color(red: 0.361, green: 0.380, blue: 0.400) // #5C6166
    static let textTertiary  = Color(red: 0.608, green: 0.620, blue: 0.639) // #9B9EA3
    static let textMuted     = Color(red: 0.761, green: 0.773, blue: 0.792) // #C2C5CA

    // MARK: - 暗色套（robinhood dark）
    static let darkCanvas   = Color(red: 0.000, green: 0.000, blue: 0.000) // #000000
    static let darkSurface1 = Color(red: 0.094, green: 0.106, blue: 0.122) // #181B1F
    static let darkSurface2 = Color(red: 0.137, green: 0.153, blue: 0.176) // #23272D
    static let darkDivider  = Color(red: 0.176, green: 0.192, blue: 0.220) // #2D3138
    static let darkTextPrimary   = Color(red: 1.000, green: 1.000, blue: 1.000) // #FFFFFF
    static let darkTextSecondary = Color(red: 0.643, green: 0.659, blue: 0.678) // #A4A8AD

    // MARK: - 品牌（robinhood 绿作强调色）
    static let brand        = Color(red: 0.000, green: 0.784, blue: 0.020) // #00C805
    static let brandPressed = Color(red: 0.000, green: 0.659, blue: 0.016) // #00A904

    // MARK: - 涨跌语义色（⚠️ A 股红涨绿跌，与国际 App 反转）
    // 涨 = 红：采用 robinhood 真红 #E62232（比其品牌橙 #FF5000 更贴近 A 股红）
    static let semanticUp   = Color(red: 0.902, green: 0.133, blue: 0.196) // #E62232 涨·红
    // 跌 = 绿：采用 robinhood 品牌绿（按下态 #00A904，比 #00C805 更沉稳易读）
    static let semanticDown = Color(red: 0.000, green: 0.659, blue: 0.016) // #00A904 跌·绿
    static let semanticFlat = Color(red: 0.608, green: 0.620, blue: 0.639) // #9B9EA3 平

    // 涨跌浅底（沿用 robinhood token：红浅底 #FFEDE5 / 绿浅底 #E6F9E0）
    static let upBg   = Color(red: 1.000, green: 0.929, blue: 0.898) // #FFEDE5 红涨浅底
    static let downBg = Color(red: 0.902, green: 0.976, blue: 0.878) // #E6F9E0 绿跌浅底

    // MARK: - 功能色
    static let info    = Color(red: 0.114, green: 0.435, blue: 0.949) // #1D6FF2
    static let warning = Color(red: 1.000, green: 0.722, blue: 0.000) // #FFB800

    // MARK: - 兼容保留：系统语义表面（自动适配暗色，无需手动切色）
    static let appSurface   = Color(.secondarySystemBackground)
    static let appSeparator = Color(.separator)

    /// 按涨跌返回语义色（红涨绿跌）
    static func trend(_ trend: Trend) -> Color {
        switch trend {
        case .up:   return semanticUp
        case .down: return semanticDown
        case .flat: return semanticFlat
        }
    }
}

extension View {
    /// 绑定涨跌语义色（红涨绿跌）
    func trendColor(_ trend: Trend) -> some View {
        foregroundStyle(Color.trend(trend))
    }
}
