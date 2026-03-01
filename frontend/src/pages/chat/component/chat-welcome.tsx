/**
 * 新对话空白态水印引导，参考 Cursor 产品提示风格。
 * 强调 RAG 与 DeepResearch 需手动开启，RAG 需先建立知识库。
 * 图标复用侧边栏与输入区工具栏真实使用的图标。
 */
import iconEdit from '@/assets/layout/edit.svg'
import iconRepository from '@/assets/layout/repository.svg'
import logo from '@/assets/logo.svg'
import { DatabaseOutlined, ExperimentOutlined } from '@ant-design/icons'
import classNames from 'classnames'
import styles from './chat-welcome.module.scss'

const TIPS = [
  {
    icon: <img src={iconRepository} alt="" className={styles['chat-welcome__tip-icon-img']} />,
    label: '知识库',
    text: '在侧边栏进入「知识库」上传文档建立知识库，RAG 需先有知识库才能启用',
  },
  {
    icon: <DatabaseOutlined />,
    label: 'RAG 检索',
    text: '在输入框下方工具栏点击 RAG 检索图标开启，开启后从下拉框选择知识库，基于自建知识库检索增强',
  },
  {
    icon: <ExperimentOutlined />,
    label: '深度研究',
    text: '在输入框下方工具栏点击深度研究图标开启，支持多轮规划、论文检索与报告生成',
  },
  {
    icon: <img src={iconEdit} alt="" className={styles['chat-welcome__tip-icon-img']} />,
    label: 'Doc Studio',
    text: '在侧边栏进入 Doc Studio，支持 LaTeX/Markdown 智能编辑，自然语言指令驱动修改与引用管理，支持论文检索与 PDF 编译预览',
  },
] as const

export default function ChatWelcome() {
  return (
    <div className={classNames(styles['chat-welcome'])}>
      <div className={styles['chat-welcome__logo-wrap']}>
        <img src={logo} alt="ScholarMind" className={styles['chat-welcome__logo']} />
      </div>
      <div className={styles['chat-welcome__title']}>如何开始</div>
      <ul className={styles['chat-welcome__tips']}>
        {TIPS.map((tip, idx) => (
          <li key={idx} className={styles['chat-welcome__tip']}>
            <span className={styles['chat-welcome__tip-badge']}>{tip.icon}</span>
            <div className={styles['chat-welcome__tip-content']}>
              <strong>{tip.label}</strong>
              <span>：{tip.text}</span>
            </div>
          </li>
        ))}
      </ul>
      <div className={styles['chat-welcome__note']}>
        RAG 与深度研究默认关闭，需在对话中手动开启。
      </div>
    </div>
  )
}
