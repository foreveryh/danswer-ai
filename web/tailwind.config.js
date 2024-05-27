/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",

    // Or if using `src` directory:
    "./src/**/*.{js,ts,jsx,tsx,mdx}",

    // tremor
    "./node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    transparent: "transparent",
    current: "currentColor",
    extend: {
      screens: {
        "2xl": "1420px",
        "3xl": "1700px",
      },
      fontFamily: {
        //sans: ["var(--font-inter)"],
        sans: ['Arial', 'Helvetica', 'sans-serif'],
      },
      width: {
        "message-xs": "450px",
        "message-sm": "550px",
        "message-default": "740px",
        "searchbar-xs": "560px",
        "searchbar-sm": "660px",
        searchbar: "850px",
        "document-sidebar": "800px",
        "document-sidebar-large": "1000px",
      },
      maxWidth: {
        "document-sidebar": "1000px",
      },
      colors: {
        // background
        background: "#f9fafb", // gray-50
        "background-emphasis": "#f6f7f8",
        "background-strong": "#eaecef",
        "background-search": "#ffffff",
        "background-custom-header": "#f3f4f6",
        "background-inverted": "#000000",

        // text or icons
        link: "#3b82f6", // blue-500
        "link-hover": "#1d4ed8", // blue-700
        subtle: "#6b7280", // gray-500
        default: "#4b5563", // gray-600
        emphasis: "#374151", // gray-700
        strong: "#111827", // gray-900
        inverted: "#ffffff", // white
        error: "#ef4444", // red-500
        success: "#059669", // emerald-600
        alert: "#f59e0b", // amber-600
        accent: "#6671d0",

        // borders
        border: "#e5e7eb", // gray-200
        "border-light": "#f3f4f6", // gray-100
        "border-strong": "#9ca3af", // gray-400

        // hover
        "hover-light": "#f3f4f6", // gray-100
        hover: "#e5e7eb", // gray-200
        "hover-emphasis": "#d1d5db", // gray-300
        "accent-hover": "#5964c2",

        // keyword highlighting
        highlight: {
          text: "#fef9c3", // yellow-100
        },

        // scrollbar
        scrollbar: {
          track: "#f9fafb",
          thumb: "#e5e7eb",
          "thumb-hover": "#d1d5db",

          dark: {
            thumb: "#989a9c",
            "thumb-hover": "#c7cdd2",
          },
        },

        // bubbles in chat for each "user"
        user: "#fb7185", // yellow-400
        ai: "#60a5fa", // blue-400

        // for display documents
        document: "#ec4899", // pink-500

        // light mode
        tremor: {
          brand: {
            faint: "#E0F2FE", // 更浅的蓝色，类似blue-50
            muted: "#93C5FD", // 轻柔的蓝色，类似blue-300
            subtle: "#60A5FA", // 标准的亮蓝色，保持原有
            DEFAULT: "#3B82F6", // 标准的蓝色，保持原有
            emphasis: "#1D4ED8", // 较深的蓝色，保持原有
            inverted: "#FFFFFF", // 白色，保持原有
          },
          background: {
            muted: "#F8FAFC", // 更浅的灰色，类似gray-50
            subtle: "#F1F5F9", // 稍深一点的灰色，类似gray-100
            DEFAULT: "#FFFFFF", // 白色背景，保持原有
            emphasis: "#1E293B", // 较深的灰色，类似gray-800，以加强对比
          },
          border: {
            DEFAULT: "#D1D5DB", // 灰色，稍深一点，类似gray-300
          },
          ring: {
            DEFAULT: "#D1D5DB", // 与边框颜色一致，类似gray-300
          },
          content: {
            subtle: "#6B7280", // 灰色，稍浅一点，类似gray-500
            DEFAULT: "#4B5563", // 中等灰色，保持原有
            emphasis: "#374151", // 较深的灰色，保持原有
            strong: "#111827", // 最深的灰色，保持原有
            inverted: "#FFFFFF", // 白色，保持原有
          },
        },
        // dark mode
        "dark-tremor": {
          brand: {
            faint: "#0B1229", // custom
            muted: "#172554", // blue-950
            subtle: "#1e40af", // blue-800
            DEFAULT: "#3b82f6", // blue-500
            emphasis: "#60a5fa", // blue-400
            inverted: "#030712", // gray-950
          },
          background: {
            muted: "#131A2B", // custom
            subtle: "#1f2937", // gray-800
            DEFAULT: "#111827", // gray-900
            emphasis: "#d1d5db", // gray-300
          },
          border: {
            DEFAULT: "#1f2937", // gray-800
          },
          ring: {
            DEFAULT: "#1f2937", // gray-800
          },
          content: {
            subtle: "#6b7280", // gray-500
            DEFAULT: "#d1d5db", // gray-300
            emphasis: "#f3f4f6", // gray-100
            strong: "#f9fafb", // gray-50
            inverted: "#000000", // black
          },
        },
      },
      boxShadow: {
        "tremor-input": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",  // 轻微增加阴影强度，增强输入框的聚焦感
        "tremor-card": "0 2px 4px -1px rgba(0, 0, 0, 0.1)",  // 统一卡片阴影，使其更柔和且适用于多种背景
        "tremor-dropdown": "0 2px 8px -2px rgba(0, 0, 0, 0.1)",  // 增加下拉菜单的阴影，以增强浮动效果
        // 保持暗模式下的阴影与亮模式相同，简化设计
        "dark-tremor-input": "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
        "dark-tremor-card": "0 2px 4px -1px rgba(0, 0, 0, 0.1)",
        "dark-tremor-dropdown": "0 2px 8px -2px rgba(0, 0, 0, 0.1)",
      },
      borderRadius: {
        "tremor-small": "0.25rem",  // 稍微减少小圆角，使其更细腻
        "tremor-default": "0.5rem",  // 保持默认圆角，适用于大多数组件
        "tremor-full": "9999px",  // 全圆角，用于圆形按钮或图标
      },
      fontSize: {
        "tremor-label": ["0.75rem", { lineHeight: "1rem" }],  // 小标签文字，稍微调整行高以适应更紧凑的设计
        "tremor-default": ["0.875rem", { lineHeight: "1.5rem" }], // 默认文本大小，适当增加行高以提高可读性
        "tremor-title": ["1.25rem", { lineHeight: "1.75rem" }], // 标题大小，适当增加字体大小，行高保持适中，符合现代设计标准
        "tremor-metric": ["2rem", { lineHeight: "2.5rem" }], // 大号数据展示，增大字体和行高，使其更显眼
      },
    },
  },
  safelist: [
    {
      pattern:
        /^(bg-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
      variants: ["hover", "ui-selected"],
    },
    {
      pattern:
        /^(text-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
      variants: ["hover", "ui-selected"],
    },
    {
      pattern:
        /^(border-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
      variants: ["hover", "ui-selected"],
    },
    {
      pattern:
        /^(ring-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
    },
    {
      pattern:
        /^(stroke-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
    },
    {
      pattern:
        /^(fill-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950))$/,
    },
  ],
  plugins: [
    require("@tailwindcss/typography"),
    require("@headlessui/tailwindcss"),
  ],
};
