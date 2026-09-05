import Foundation

public struct MacroReference: Identifiable, Hashable, Sendable {
    public let id: String
    public let condition: String
    public let companyProfile: String
    public let explanation: String

    public init(id: String, condition: String, companyProfile: String, explanation: String) {
        self.id = id
        self.condition = condition
        self.companyProfile = companyProfile
        self.explanation = explanation
    }
}

public enum MacroReferences {
    // These are the established explanations from the original app's
    // MACRO_REFERENCE_ROWS. They intentionally remain deterministic copy.
    public static let bySeriesID: [String: MacroReference] = [
        "DGS10": MacroReference(id: "DGS10", condition: "Rising",
            companyProfile: "Long-duration growth stocks and rate-sensitive businesses face greater valuation pressure",
            explanation: "The 10-year Treasury yield is a market borrowing-rate benchmark, distinct from the Fed's overnight rate. Higher yields can reduce the present value of distant profits and raise financing costs. They may also reflect stronger growth rather than tighter policy, so compare the move with inflation and credit spreads before drawing an equity conclusion."),
        "UNRATE": MacroReference(id: "UNRATE", condition: "Rising",
            companyProfile: "Consumer-sensitive and cyclical earnings may face weaker demand",
            explanation: "Rising unemployment can signal weakening household income and demand. That can pressure discretionary spending and cyclical earnings, while weaker wage pressure may allow easier monetary policy. The rate can also change because people enter or leave the labor force, so one monthly observation does not establish a recession."),
        "DFF": MacroReference(
            id: "DFF",
            condition: "High or rising",
            companyProfile: "Profitable, low-leverage companies (less dependent on distant future profits or expensive refinancing)",
            explanation: "Higher rates mean investors can earn more risk-free, so they are less willing to pay as much today for profits that arrive far in the future. This especially hurts high-growth companies because more of their value comes from distant future profits. Higher rates also make bank loans and corporate bonds more expensive, so high-leverage companies pay more interest when they refinance debt. That reduces expected profits and equity value. Both mechanisms can lower stock prices."
        ),
        "WALCL": MacroReference(
            id: "WALCL",
            condition: "Falling",
            companyProfile: "Profitable, low-leverage companies (less dependent on distant future profits or expensive refinancing)",
            explanation: "The Fed owns a large amount of Treasury bonds. Normally, when those bonds mature, the Fed can use the repayment to buy replacement Treasuries. When the Fed shrinks its balance sheet, it stops replacing some of the bonds that mature. As a result, a larger share of Treasury debt must be held by investors other than the Fed. If investors will not hold that additional amount at existing prices, Treasury prices fall (and Treasury yields increase) until the higher yields make them attractive enough to buy. Higher Treasury yields then hurt high-growth companies because investors can earn more risk-free, so distant future profits are worth less today. They hurt high-leverage companies because corporate borrowing and refinancing rates also tend to rise, increasing interest expense and reducing expected profits. Both effects can reduce equity value and stock price."
        ),
        "BAMLH0A0HYM2": MacroReference(
            id: "BAMLH0A0HYM2",
            condition: "High or rising",
            companyProfile: "Profitable, low-leverage companies (less debt exposed to expensive refinancing)",
            explanation: "A high-yield spread is the extra interest rate risky companies must pay above the Treasury rate. For example, if Treasuries yield 4% and a company has a 4% credit spread, investors will demand roughly 8% to lend to it. If the spread rises, refinancing becomes more expensive even if Treasury rates do not change. Companies with lots of debt experience a larger increase in total interest expense, reducing expected profits and cash available to shareholders. That lowers equity value and stock price."
        ),
        "VIXCLS": MacroReference(
            id: "VIXCLS",
            condition: "High or rising sharply",
            companyProfile: "Profitable, stable companies (less uncertainty about future profits)",
            explanation: "The VIX is mainly a signal of how much volatility investors expect, rather than something that mechanically causes stock prices to fall. When uncertainty is high, investors generally require a higher expected return to take stock-market risk. If the company’s expected future profits are unchanged, investors must pay a lower price today to earn that higher expected return. That means lower equity value and stock price, particularly for companies investors consider risky or uncertain."
        ),
        "CPIAUCNS": MacroReference(
            id: "CPIAUCNS",
            condition: "High or accelerating",
            companyProfile: "Profitable companies with pricing power (can raise prices enough to offset higher costs)",
            explanation: "Inflation can raise wages, materials, transportation, and other costs. If a company cannot raise the prices it charges customers enough to compensate, its profit margins shrink. Lower expected future profits mean lower equity value and therefore a lower stock price. High inflation can also cause interest rates to stay higher because the Fed may keep borrowing costs elevated to slow spending and investment, reduce demand in the economy, and bring inflation back down. Higher rates then create the additional high-growth and high-leverage effects described above."
        ),
    ]
}
