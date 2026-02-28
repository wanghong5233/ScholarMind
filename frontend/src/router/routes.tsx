import { AdminLayout } from '@/layout/admin'
import { BaseLayout } from '@/layout/base'
import NotFound from '@/pages/404'
import AdminPage from '@/pages/admin'
import AdminForbiddenPage from '@/pages/admin/forbidden'
import AdminLoginPage from '@/pages/admin/login'
import Chat from '@/pages/chat'
import Index from '@/pages/index'
import Login from '@/pages/login'
import Repository from '@/pages/repository'
import DemoEntryPage from '@/pages/demo-entry'
import ParseDebugPage from '@/pages/debug/parse'
import RetrievalDebugPage from '@/pages/debug/retrieval'
import RepositoryOnlineImport from '@/pages/repository/OnlineImport'
import DocStudioPage from '@/pages/doc-studio'
import DeepResearchPage from '@/pages/deep-research'
import IdeaGenerationPage from '@/pages/idea-generation'
import {
  Navigate,
  Outlet,
  RouteObject,
  createBrowserRouter,
  useLocation,
} from 'react-router-dom'
import { RouterGuard } from './guard'

const ENABLE_ADMIN_UI = String(import.meta.env.VITE_ENABLE_ADMIN_UI ?? 'true').toLowerCase() !== 'false'

function RedirectToAdminParse() {
  return <Navigate to="/admin/debug/parse" replace />
}

function RedirectToAdminRetrieval() {
  return <Navigate to="/admin/debug/retrieval" replace />
}

function RedirectChatAdminToAdmin() {
  return <Navigate to="/admin" replace />
}

export type IRouteObject = {
  children?: IRouteObject[]
  name?: string
  auth?: boolean
  pure?: boolean
  meta?: any
} & Omit<RouteObject, 'children'>

export const routes: IRouteObject[] = [
  {
    path: '/',
    Component: Index,
  },
  {
    path: '/chat',
    Component: Chat,
  },
  ...(ENABLE_ADMIN_UI
    ? [
        {
          path: '/chat/admin',
          Component: RedirectChatAdminToAdmin,
        } as IRouteObject,
      ]
    : []),
  {
    path: '/chat/:id',
    Component: Chat,
  },
  {
    path: '/repository',
    Component: Repository,
  },
  {
    path: '/repository/:kbId/online-import',
    Component: RepositoryOnlineImport,
  },
  ...(ENABLE_ADMIN_UI
    ? [
        {
          path: '/debug/parse',
          Component: RedirectToAdminParse,
        } as IRouteObject,
        {
          path: '/debug/retrieval',
          Component: RedirectToAdminRetrieval,
        } as IRouteObject,
      ]
    : []),
  {
    path: '/doc-studio/:workspaceId?',
    Component: DocStudioPage,
  },
  {
    path: '/deep-research',
    Component: DeepResearchPage,
  },
  {
    path: '/idea-generation',
    Component: IdeaGenerationPage,
  },
]

const adminRoutes: IRouteObject[] = [
  {
    index: true,
    Component: AdminPage,
    meta: {
      admin: true,
    },
  },
  {
    path: 'debug/parse',
    Component: ParseDebugPage,
    meta: {
      admin: true,
    },
  },
  {
    path: 'debug/retrieval',
    Component: RetrievalDebugPage,
    meta: {
      admin: true,
    },
  },
  {
    path: 'forbidden',
    Component: AdminForbiddenPage,
    auth: false,
  },
]

function Layout() {
  const location = useLocation()
  return (
    <BaseLayout>
      <RouterGuard>
        <Outlet key={location.pathname} />
      </RouterGuard>
    </BaseLayout>
  )
}

function AdminRouteLayout() {
  const location = useLocation()
  return (
    <AdminLayout>
      <RouterGuard>
        <Outlet key={location.pathname} />
      </RouterGuard>
    </AdminLayout>
  )
}

export const router = createBrowserRouter(
  [
    helper({
      path: '/',
      Component: Layout,
      children: routes,
    }),
    ...(ENABLE_ADMIN_UI
      ? [
          helper({
            path: '/admin',
            Component: AdminRouteLayout,
            children: adminRoutes,
          }),
        ]
      : []),
    helper({
      path: '/login',
      Component: Login,
      auth: false,
    }),
    helper({
      path: '/demo',
      Component: DemoEntryPage,
      auth: false,
      pure: true,
    }),
    ...(ENABLE_ADMIN_UI
      ? [
          helper({
            path: '/admin/login',
            Component: AdminLoginPage,
            auth: false,
            pure: true,
          }),
        ]
      : []),
    helper({
      path: '404',
      Component: NotFound,
      pure: true,
    }),
    helper({
      path: '*',
      Component: NotFound,
    }),
  ],
  {
    basename: import.meta.env.BASE_URL,
  },
)

function helper(route: IRouteObject) {
  const _route = {
    ...route,
  }

  if (_route.children) {
    _route.children = _route.children.map((child: any) => helper(child))
  }

  if (_route.auth === undefined) {
    _route.auth = true
  }

  return _route as RouteObject
}
